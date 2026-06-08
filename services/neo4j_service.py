"""
Neo4j sync service — keeps the garment graph in sync with PostgreSQL.
All operations are fire-and-forget: failures are logged but never crash the main flow.
"""
import os
import logging
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

_driver = None


def _get_driver():
    global _driver
    if _driver is not None:
        return _driver
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
        from neo4j import GraphDatabase
        uri  = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER",     "neo4j")
        pwd  = os.getenv("NEO4J_PASSWORD")
        if not pwd:
            logger.warning("⚠️  NEO4J_PASSWORD not set — Neo4j sync disabled")
            return None
        _driver = GraphDatabase.driver(uri, auth=(user, pwd))
        _driver.verify_connectivity()
        logger.info("✅ Neo4j connected (%s)", uri)
    except Exception as e:
        logger.warning("⚠️  Neo4j unavailable — graph sync disabled (%s)", e)
        _driver = None
    return _driver


def upsert_garment(garment_id: str, user_id: str, attrs: Dict) -> None:
    """
    Create or update a Garment node in Neo4j.
    Also creates a (User)-[:OWNS]->(Garment) edge.
    """
    driver = _get_driver()
    if not driver:
        return
    try:
        with driver.session() as session:
            session.run(
                """
                MERGE (u:User {id: $user_id})
                MERGE (g:Garment {id: $garment_id})
                SET g.user_id     = $user_id,
                    g.category    = $category,
                    g.subcategory = $subcategory,
                    g.color       = $color,
                    g.color_hex   = $color_hex,
                    g.pattern     = $pattern,
                    g.material    = $material,
                    g.formality   = $formality,
                    g.image_url   = $image_url,
                    g.source      = 'algostyle'
                MERGE (u)-[:OWNS]->(g)
                """,
                garment_id = garment_id,
                user_id    = user_id,
                category   = attrs.get("category", ""),
                subcategory= attrs.get("subcategory", ""),
                color      = attrs.get("color_primary", ""),
                color_hex  = attrs.get("color_hex", ""),
                pattern    = attrs.get("pattern", ""),
                material   = attrs.get("material", ""),
                formality  = attrs.get("formality", ""),
                image_url  = attrs.get("image_url", ""),
            )
    except Exception as e:
        logger.warning("Neo4j upsert_garment failed for %s: %s", garment_id, e)


def get_garments_by_ids(garment_ids: list) -> dict:
    """
    Fetch garment properties from Neo4j by a list of IDs.
    Returns a dict keyed by garment id: {id: {field: value, ...}}.
    Falls back to an empty dict if Neo4j is unavailable.
    """
    driver = _get_driver()
    if not driver or not garment_ids:
        return {}
    try:
        with driver.session() as session:
            result = session.run(
                "MATCH (g:Garment) WHERE g.id IN $ids RETURN g",
                ids=garment_ids,
            )
            return {record["g"]["id"]: dict(record["g"]) for record in result}
    except Exception as e:
        logger.warning("Neo4j get_garments_by_ids failed: %s", e)
        return {}


def delete_garment(garment_id: str) -> None:
    """
    Detach-delete the Garment node and all its relationships from Neo4j.
    """
    driver = _get_driver()
    if not driver:
        return
    try:
        with driver.session() as session:
            result = session.run(
                "MATCH (g:Garment {id: $id}) DETACH DELETE g RETURN count(g) AS deleted",
                id=garment_id,
            )
            record = result.single()
            count = record["deleted"] if record else 0
            if count:
                logger.info("Neo4j: deleted Garment node %s", garment_id)
            else:
                logger.debug("Neo4j: no Garment node found for %s (nothing to delete)", garment_id)
    except Exception as e:
        logger.warning("Neo4j delete_garment failed for %s: %s", garment_id, e)


def create_compatibility_relations(
    garment_id: str,
    compatible_ids: list[tuple[str, float]],
) -> None:
    """
    Create COMPATIBLE_WITH relationships in Neo4j between a garment
    and its most compatible peers.

    compatible_ids — list of (other_garment_id, score) tuples,
                     score in [0, 1].  Typically the top-N from Layer 2.

    Both garments must already exist as nodes (created via upsert_garment).
    This is a fire-and-forget call: failures are logged and never propagate.
    """
    driver = _get_driver()
    if not driver or not compatible_ids:
        return
    try:
        with driver.session() as session:
            for other_id, score in compatible_ids:
                session.run(
                    """
                    MATCH (a:Garment {id: $gid}), (b:Garment {id: $oid})
                    MERGE (a)-[r:COMPATIBLE_WITH]->(b)
                    SET r.score      = $score,
                        r.updated_at = datetime()
                    MERGE (b)-[r2:COMPATIBLE_WITH]->(a)
                    SET r2.score      = $score,
                        r2.updated_at = datetime()
                    """,
                    gid=garment_id,
                    oid=other_id,
                    score=float(score),
                )
        logger.info(
            "Neo4j: created %d COMPATIBLE_WITH relations for garment %s",
            len(compatible_ids), garment_id,
        )
    except Exception as e:
        logger.warning(
            "Neo4j create_compatibility_relations failed for %s: %s",
            garment_id, e,
        )

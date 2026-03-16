"""
Neo4j sync service — keeps the garment graph in sync with PostgreSQL.
All operations are fire-and-forget: failures are logged but never crash the main flow.
"""
import os
import logging
from typing import Optional, Dict

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
        pwd  = os.getenv("NEO4J_PASSWORD", "neo4j_password_123")
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

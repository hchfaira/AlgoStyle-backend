# 🧪 Guide — Tests réels de l'API mobile (image consulting)

## Architecture en jeu

```
📱 Mobile (Expo)          🖥️  Backend (FastAPI)         🗄️  Base de données
useImageConsulting.ts  →  POST /api/v1/image-           PostgreSQL
  USE_MOCK = false          consulting/analyze/:userId     (tables: users,
                                 ↓                          user_profiles,
                           image_consulting_service.py      image_consulting_results)
                           → _mock_result()  ← règles
                           (futur: LLM_project pipeline)
```

---

## Étape 1 — Lancer PostgreSQL avec Docker

Le backend attend PostgreSQL sur `localhost:5432`.
Aucune installation nécessaire — Docker suffit.

```bash
# Depuis n'importe quel dossier
docker run -d \
  --name algostyle_pg \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=algostyle \
  -p 5432:5432 \
  postgres:16-alpine

# Vérifier que c'est bien démarré
docker ps | grep algostyle_pg
```

> **Important** : Neo4j (déjà en cours) est pour `LLM_project` uniquement.
> Le backend AlgoStyle utilise **PostgreSQL**, pas Neo4j.

---

## Étape 2 — Configurer le `.env` du backend

```bash
cd /home/chfaira-hajar/work/repos/algoStyle_app/backend

cat > .env << 'EOF'
ENVIRONMENT=development
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/algostyle
CORS_ORIGINS=http://localhost:8081,http://localhost:3000,http://192.168.1.71:8000
CORS_CREDENTIALS=true
EOF
```

---

## Étape 3 — Installer les dépendances et démarrer le backend

```bash
cd /home/chfaira-hajar/work/repos/algoStyle_app/backend

# Créer un venv isolé pour le backend
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

# Démarrer le serveur (crée les tables automatiquement au démarrage)
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Si tout est bon tu verras :
```
✅ Database tables initialized
✅ Database connection successful
✅ Database ready for requests
INFO:     Uvicorn running on http://0.0.0.0:8000
```

Swagger UI disponible sur : http://localhost:8000/docs

---

## Étape 4 — Configurer le mobile pour pointer vers le backend réel

```bash
# Trouver ton IP locale (le mobile doit pouvoir joindre le backend)
ip addr show | grep "inet " | grep -v 127.0.0.1
# ex: 192.168.1.71
```

Dans le mobile, dans `constants/config.ts` (ou équivalent) :
```ts
export const API_BASE_URL = 'http://192.168.1.71:8000';
// Pas localhost — le mobile physique/émulateur ne peut pas résoudre localhost
```

Et dans `hooks/useImageConsulting.ts` :
```ts
const USE_MOCK = false;  // ← désactiver le mock
```

---

## Étape 5 — Test rapide avec curl (sans mobile)

### 5.1 — Créer un utilisateur
```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"test@algostyle.com","password":"Test1234!","name":"Hajar Test","role":"user"}'
# → {"token": "...", "user_id": "user_xxxx", ...}
```

### 5.2 — Sauvegarder user_id et token
```bash
export USER_ID="user_xxxx"   # remplacer par la valeur réelle
export TOKEN="eyJ..."        # remplacer par le token reçu
```

### 5.3 — Envoyer une vraie image pour l'analyse
```bash
# Utiliser une image de test du LLM_project
curl -X POST "http://localhost:8000/api/v1/image-consulting/analyze/${USER_ID}" \
  -H "Authorization: Bearer ${TOKEN}" \
  -F "image=@/home/chfaira-hajar/work/repos/LLM_project/data/people/image.png" \
  -F "height_cm=168" \
  -F "weight_kg=62"
```

### 5.4 — Récupérer le résultat sauvegardé
```bash
curl "http://localhost:8000/api/v1/image-consulting/result/${USER_ID}" \
  -H "Authorization: Bearer ${TOKEN}"
```

---

## Étape 6 — Brancher le vrai pipeline LLM_project

En ce moment, `image_consulting_service.py` utilise `_mock_result()` (règles déterministes).
Pour brancher le vrai pipeline de `LLM_project` :

### 6.1 — Ajouter LLM_project au PYTHONPATH du backend

```bash
# Dans le .env du backend, ajouter :
LLM_PROJECT_PATH=/home/chfaira-hajar/work/repos/LLM_project
```

### 6.2 — Modifier `analyze_image()` dans `image_consulting_service.py`

Remplacer `result = _mock_result(...)` par :

```python
import sys
import os
llm_path = os.getenv("LLM_PROJECT_PATH")
if llm_path:
    sys.path.insert(0, llm_path)
    from src.layer3_context.user_profile import StyleProfilePipeline, PipelineConfig
    from io import BytesIO
    from PIL import Image as PILImage

    # Appel du vrai pipeline
    config = PipelineConfig(
        enable_body_analysis=True,
        enable_face_analysis=True,
        enable_color_analysis=True,
        enable_hair_analysis=True,
        enable_contrast_analysis=True,
    )
    pipeline = StyleProfilePipeline(config)
    pil_img = PILImage.open(BytesIO(image_bytes))
    pipeline_result = pipeline.analyze(pil_img, height_cm=height_cm, weight_kg=weight_kg)
    profile = pipeline_result.profile

    # Mapper StyleProfile → ImageConsultingResult
    result = ImageConsultingResult(
        user_id=user_id,
        body_shape=profile.body_shape.value if profile.body_shape else None,
        face_shape=profile.face_shape.value if profile.face_shape else None,
        skin_tone=profile.skin_tone.value if profile.skin_tone else None,
        undertone=profile.undertone.value if profile.undertone else None,
        hair_color=profile.hair_color.value if profile.hair_color else None,
        contrast_level=profile.contrast_level.value if profile.contrast_level else None,
        visual_weight=profile.visual_weight.value if profile.visual_weight else "medium",
        color_season=_compute_season(
            profile.undertone.value if profile.undertone else "neutral",
            profile.contrast_level.value if profile.contrast_level else "medium",
        ),
        estimated_top_size=profile.body_metrics.estimated_top_size if profile.body_metrics else None,
        estimated_bottom_size=profile.body_metrics.estimated_bottom_size if profile.body_metrics else None,
        color_palette=_build_color_palette(
            profile.undertone.value if profile.undertone else "neutral",
            profile.skin_tone.value if profile.skin_tone else "medium",
            profile.contrast_level.value if profile.contrast_level else "medium",
        ),
        body_shape_guidance=_build_body_guidance(
            profile.body_shape.value if profile.body_shape else "rectangle"
        ),
        face_shape_guidance=_build_face_guidance(
            profile.face_shape.value if profile.face_shape else "oval"
        ),
        summary=_build_summary(
            profile.body_shape.value if profile.body_shape else "rectangle",
            profile.skin_tone.value if profile.skin_tone else "medium",
            profile.undertone.value if profile.undertone else "neutral",
            profile.contrast_level.value if profile.contrast_level else "medium",
        ),
        overall_confidence=pipeline_result.overall_confidence,
    )
else:
    result = _mock_result(user_id, height_cm, weight_kg)
```

---

## Résumé — Quelle base de données, où ?

| Composant | Base de données | Port | Usage |
|-----------|----------------|------|-------|
| **AlgoStyle Backend** | **PostgreSQL** | **5432** | Users, tokens, profiles, wardrobe, image consulting results |
| **LLM_project** | Neo4j (déjà up) | 7687 | Graph outfits/garments (pipeline Neo4j) |
| LLM_project | Fichiers locaux | — | Images, embeddings, processed data |

**Tu n'as qu'une chose à lancer : PostgreSQL sur le port 5432.**

---

## Script tout-en-un — `start_backend.sh`

```bash
#!/bin/bash
set -e

echo "🐘 Starting PostgreSQL..."
docker start algostyle_pg 2>/dev/null || \
  docker run -d --name algostyle_pg \
    -e POSTGRES_USER=postgres \
    -e POSTGRES_PASSWORD=postgres \
    -e POSTGRES_DB=algostyle \
    -p 5432:5432 \
    postgres:16-alpine

echo "⏳ Waiting for PostgreSQL..."
until docker exec algostyle_pg pg_isready -U postgres > /dev/null 2>&1; do
  sleep 1
done

echo "🚀 Starting FastAPI backend..."
cd /home/chfaira-hajar/work/repos/algoStyle_app/backend
source .venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

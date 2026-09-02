"""Relecture assistée par modèle de langue (§16, §27).

La relecture est une **collation** : l'image de la page d'origine est mise en
regard du texte que la chaîne en a tiré, et le modèle rapporte les écarts. Ce
qu'il doit écrire, c'est ce qui figure sur l'image — jamais ce qui devrait
logiquement s'y trouver. Chaque écart est ensuite borné mécaniquement avant
d'être appliqué ; ce qui ne tient pas est signalé, pas appliqué. La relecture
ne valide jamais : elle prépare une décision humaine.
"""

from bldp.core.review.batch import (
    BatchOutcome,
    PlannedDocument,
    ReviewPlan,
    plan_review,
    run_review,
)
from bldp.core.review.client import (
    API_KEY_ENV,
    CallReport,
    ReviewCallError,
    ReviewClient,
    ReviewUnavailableError,
    api_key_present,
    check_ready,
    review_available,
)
from bldp.core.review.corrections import (
    ArticleRef,
    Correction,
    Finding,
    SourceContext,
    letter_similarity,
    verify_all,
    verify_correction,
)
from bldp.core.review.page_images import (
    PageImage,
    PageImageError,
    estimate_images,
    render_document,
    render_pages,
    source_pdf,
)
from bldp.core.review.reviewer import (
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
    ReviewResult,
    apply_corrections,
    build_collation_content,
    build_message,
    prepare_content,
    review_document,
)

__all__ = [
    "API_KEY_ENV",
    "ArticleRef",
    "BatchOutcome",
    "CallReport",
    "Correction",
    "Finding",
    "PageImage",
    "PageImageError",
    "PlannedDocument",
    "RESPONSE_SCHEMA",
    "ReviewCallError",
    "ReviewClient",
    "ReviewPlan",
    "ReviewResult",
    "ReviewUnavailableError",
    "SYSTEM_PROMPT",
    "SourceContext",
    "api_key_present",
    "apply_corrections",
    "build_collation_content",
    "build_message",
    "check_ready",
    "estimate_images",
    "letter_similarity",
    "plan_review",
    "prepare_content",
    "render_document",
    "render_pages",
    "review_available",
    "review_document",
    "run_review",
    "source_pdf",
    "verify_all",
    "verify_correction",
]

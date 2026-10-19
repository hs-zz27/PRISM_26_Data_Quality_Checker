CREATE TABLE sample_asset_manifests (
    dataset_id TEXT NOT NULL CHECK (length(btrim(dataset_id)) > 0),
    sample_id TEXT NOT NULL CHECK (length(btrim(sample_id)) > 0),
    sample_revision INTEGER NOT NULL CHECK (sample_revision >= 1),

    original_image_availability TEXT NOT NULL CHECK (
        original_image_availability IN ('available', 'missing', 'unreadable')
    ),
    original_image_storage_key TEXT,
    original_image_content_type TEXT,
    original_image_sha256 TEXT,
    original_image_size_bytes BIGINT,

    raw_mask_availability TEXT NOT NULL CHECK (
        raw_mask_availability IN ('available', 'missing', 'unreadable')
    ),
    raw_mask_storage_key TEXT,
    raw_mask_content_type TEXT,
    raw_mask_sha256 TEXT,
    raw_mask_size_bytes BIGINT,

    annotation_text_availability TEXT NOT NULL CHECK (
        annotation_text_availability IN ('available', 'missing', 'unreadable')
    ),
    annotation_text_storage_key TEXT,
    annotation_text_content_type TEXT,
    annotation_text_sha256 TEXT,
    annotation_text_size_bytes BIGINT,

    PRIMARY KEY (dataset_id, sample_id, sample_revision),

    CHECK (
        (original_image_availability = 'available'
         AND original_image_storage_key IS NOT NULL
         AND length(btrim(original_image_storage_key)) > 0
         AND original_image_storage_key = btrim(original_image_storage_key)
         AND original_image_storage_key !~ '^/'
         AND original_image_storage_key !~ '^[A-Za-z]:'
         AND original_image_storage_key !~ '(^|/)[.][.](/|$)'
         AND original_image_storage_key !~ '(^|/)[.](/|$)'
         AND original_image_content_type IS NOT NULL
         AND length(btrim(original_image_content_type)) > 0
         AND original_image_sha256 IS NOT NULL
         AND original_image_sha256 ~ '^[0-9a-f]{64}$'
         AND original_image_size_bytes IS NOT NULL
         AND original_image_size_bytes >= 0)
        OR
        (original_image_availability IN ('missing', 'unreadable')
         AND original_image_storage_key IS NULL
         AND original_image_content_type IS NULL
         AND original_image_sha256 IS NULL
         AND original_image_size_bytes IS NULL)
    ),
    CHECK (
        (raw_mask_availability = 'available'
         AND raw_mask_storage_key IS NOT NULL
         AND length(btrim(raw_mask_storage_key)) > 0
         AND raw_mask_storage_key = btrim(raw_mask_storage_key)
         AND raw_mask_storage_key !~ '^/'
         AND raw_mask_storage_key !~ '^[A-Za-z]:'
         AND raw_mask_storage_key !~ '(^|/)[.][.](/|$)'
         AND raw_mask_storage_key !~ '(^|/)[.](/|$)'
         AND raw_mask_content_type IS NOT NULL
         AND length(btrim(raw_mask_content_type)) > 0
         AND raw_mask_sha256 IS NOT NULL
         AND raw_mask_sha256 ~ '^[0-9a-f]{64}$'
         AND raw_mask_size_bytes IS NOT NULL
         AND raw_mask_size_bytes >= 0)
        OR
        (raw_mask_availability IN ('missing', 'unreadable')
         AND raw_mask_storage_key IS NULL
         AND raw_mask_content_type IS NULL
         AND raw_mask_sha256 IS NULL
         AND raw_mask_size_bytes IS NULL)
    ),
    CHECK (
        (annotation_text_availability = 'available'
         AND annotation_text_storage_key IS NOT NULL
         AND length(btrim(annotation_text_storage_key)) > 0
         AND annotation_text_storage_key = btrim(annotation_text_storage_key)
         AND annotation_text_storage_key !~ '^/'
         AND annotation_text_storage_key !~ '^[A-Za-z]:'
         AND annotation_text_storage_key !~ '(^|/)[.][.](/|$)'
         AND annotation_text_storage_key !~ '(^|/)[.](/|$)'
         AND annotation_text_content_type IS NOT NULL
         AND length(btrim(annotation_text_content_type)) > 0
         AND annotation_text_sha256 IS NOT NULL
         AND annotation_text_sha256 ~ '^[0-9a-f]{64}$'
         AND annotation_text_size_bytes IS NOT NULL
         AND annotation_text_size_bytes >= 0)
        OR
        (annotation_text_availability IN ('missing', 'unreadable')
         AND annotation_text_storage_key IS NULL
         AND annotation_text_content_type IS NULL
         AND annotation_text_sha256 IS NULL
         AND annotation_text_size_bytes IS NULL)
    )
);

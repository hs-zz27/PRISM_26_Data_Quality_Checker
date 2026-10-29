-- Existing revisions remain Penn-Fudan. JSON formats pin the original document
-- and its image record separately; coordinates are never converted to Penn-Fudan.
ALTER TABLE datasets DROP CONSTRAINT datasets_format_check;
ALTER TABLE datasets ADD CONSTRAINT datasets_format_check
    CHECK (format IN ('penn_fudan', 'coco', 'labelme'));

ALTER TABLE sample_asset_manifests
    ADD COLUMN annotation_format TEXT NOT NULL DEFAULT 'penn_fudan'
        CHECK (annotation_format IN ('penn_fudan', 'coco', 'labelme')),
    ADD COLUMN image_key TEXT;

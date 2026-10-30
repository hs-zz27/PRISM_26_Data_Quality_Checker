-- A JSON annotation can supply polygons or embedded RLE without a standalone
-- mask file. Distinguish that from an expected file that could not be found.
-- Preserve every existing constraint clause while extending only its state set.
DO $$
DECLARE item RECORD;
BEGIN
    FOR item IN
        SELECT c.conname, pg_get_constraintdef(c.oid) AS definition
        FROM pg_constraint c JOIN pg_attribute a
          ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
        WHERE c.conrelid = 'sample_asset_manifests'::regclass AND c.contype = 'c'
          AND a.attname = 'raw_mask_availability'
    LOOP
        EXECUTE format('ALTER TABLE sample_asset_manifests DROP CONSTRAINT %I', item.conname);
        EXECUTE format('ALTER TABLE sample_asset_manifests ADD CONSTRAINT %I %s', item.conname,
            replace(item.definition, '''unreadable''::text', '''unreadable''::text, ''not_supplied''::text'));
    END LOOP;
END;
$$;
ALTER TABLE sample_asset_manifests ADD CONSTRAINT optional_mask_requires_json_format
    CHECK (raw_mask_availability <> 'not_supplied' OR annotation_format IN ('coco', 'labelme'));

-- Do not rewrite historical manifests: their bytes/hashes pin prior reviews.

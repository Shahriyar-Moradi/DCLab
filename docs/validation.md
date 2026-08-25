# Validation

Every experiment has train, validation, and test.

- **time** (default for horizon tasks): sort by prediction time; train < val < test.
- **stratified** / **random** when no time column exists.
- **group** by entity id.
- **rolling** uses the same ordered cut as time in v0.1.

Selection and blend weights use **validation**. Test is read once for the report.

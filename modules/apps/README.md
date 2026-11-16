
## Build order

`kcc` depends on the own build `python-mozjpeg-lossless-optimization`. arcus builds all own
builds before installing any, so on a fresh machine apply once with only the library in `local`,
then add `kcc` and apply again.

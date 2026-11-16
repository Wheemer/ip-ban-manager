# Vendored MaxMind DB Reader

Source: `maxminddb` 3.1.1, published on PyPI by MaxMind.

- Source archive: https://files.pythonhosted.org/packages/31/83/bcd7f2e7dfcf601258a4eab92155816218e8f8adf6608d5f7d39da7ba863/maxminddb-3.1.1.tar.gz
- Source archive SHA-256: `b19a938c481518f19a2c534ffdcb3bc59582f0fbbdcf9f81ac9adf912a0af686`
- License and copyright notices: [MAXMINDDB-LICENSE](MAXMINDDB-LICENSE).

Compared against that archive on 2026-11-16. The Python reader files are
unchanged except for these packaging and typing adjustments:

- `__init__.py` uses the literal version `3.1.1` instead of installed package
  metadata, since this bundled copy has no distribution metadata.
- `file.py` removes two obsolete type-ignore comments.
- `reader.py` declares the type of `node_bytes` and removes one obsolete
  type-ignore comment.

The C extension is not bundled. Retain these notices and repeat the source
comparison when updating the bundled reader.

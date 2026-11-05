"""Search Viral: coleta e análise de vídeos virais por nicho."""

# Usa o repositório de certificados do sistema operacional (necessário em redes
# corporativas com proxy/CA próprio, onde o bundle do certifi falha).
try:
    import truststore

    truststore.inject_into_ssl()
except Exception:  # pragma: no cover - truststore é opcional
    pass

# Imagem oficial do Playwright para Python: já traz Chromium + todas as
# libs de sistema resolvidas (Playwright não é bem suportado em Alpine/musl,
# por isso não partimos das imagens base Alpine do Home Assistant).
#
# A tag da imagem (v1.47.0) precisa bater EXATAMENTE com a versão do pacote
# "playwright" pinada em requirements.txt — divergência entre as duas causa
# "Executable doesn't exist at /ms-playwright/..." em runtime, porque o
# navegador pré-instalado na imagem é de uma revisão diferente da que o
# pacote Python espera. Ao mudar uma, mude a outra junto.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY rootfs/app /app

EXPOSE 8978

CMD ["python3", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8978"]

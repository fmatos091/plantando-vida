"""
Gerador de hashes SHA-512 dos arquivos do projeto Plantando Vida.
Execute: python gerar_hashes.py
Saida:   hashes_sha512.txt
"""

import hashlib
import os
from datetime import datetime

# Extensoes de arquivos a incluir no checksum
EXTENSOES = {
    ".py", ".html", ".css", ".js",
    ".txt", ".md", ".cfg", ".ini", ".toml",
}

# Arquivos extras sem extensao padrao
ARQUIVOS_EXTRAS = {"requirements.txt", "Procfile", ".env.example"}

# Pastas a ignorar
IGNORAR_PASTAS = {"__pycache__", ".git", "node_modules", "venv", ".venv"}

# Arquivos gerados que nao precisam de hash
IGNORAR_ARQUIVOS = {"hashes_sha512.txt", "diagrama_casos_uso.pdf"}


def sha512_arquivo(caminho):
    """Calcula o hash SHA-512 de um arquivo."""
    h = hashlib.sha512()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(65536), b""):
            h.update(bloco)
    return h.hexdigest()


def gerar(saida="hashes_sha512.txt"):
    arquivos = []
    for raiz, dirs, files in os.walk("."):
        dirs[:] = sorted(d for d in dirs if d not in IGNORAR_PASTAS)
        for nome in sorted(files):
            if nome in IGNORAR_ARQUIVOS:
                continue
            ext = os.path.splitext(nome)[1].lower()
            if ext in EXTENSOES or nome in ARQUIVOS_EXTRAS:
                caminho = os.path.join(raiz, nome)
                arquivos.append(caminho)

    linhas = [
        "# Hashes SHA-512 — Projeto Plantando Vida",
        f"# Gerado em: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"# Total de arquivos: {len(arquivos)}",
        "#",
        "# FORMATO: SHA512  CAMINHO",
        "#",
    ]

    erros = 0
    for caminho in arquivos:
        try:
            digest = sha512_arquivo(caminho)
            linhas.append(f"{digest}  {caminho}")
        except Exception as e:
            linhas.append(f"{'ERRO':128}  {caminho}  # {e}")
            erros += 1

    with open(saida, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas) + "\n")

    print(f"Arquivo gerado: {saida}")
    print(f"  {len(arquivos)} arquivos processados | {erros} erros")


if __name__ == "__main__":
    gerar()

"""
Gerador do resumo_hash.txt — "hash master" do projeto Plantando Vida.
Le o hashes_sha512.txt (gerado por gerar_hashes.py) e combina todos os
hashes individuais em um unico SHA-512 "master", que representa o estado
integro do projeto no momento da geracao.

Execute nesta ordem:
    python gerar_hashes.py        # atualiza hashes_sha512.txt
    python gerar_resumo_hash.py   # atualiza resumo_hash.txt a partir dele
"""

import hashlib
import subprocess
from datetime import datetime

ENTRADA = "hashes_sha512.txt"
SAIDA   = "resumo_hash.txt"


def ler_hashes_individuais(caminho):
    """Extrai apenas a coluna de hash (ignora comentarios e o caminho do arquivo)."""
    digests = []
    with open(caminho, "r", encoding="utf-8") as f:
        for linha in f:
            linha = linha.strip()
            if not linha or linha.startswith("#"):
                continue
            digests.append(linha.split("  ", 1)[0])
    return digests


def hash_master(digests):
    """SHA-512 da concatenacao, em ordem, de todos os hashes individuais."""
    h = hashlib.sha512()
    h.update("".join(digests).encode("utf-8"))
    return h.hexdigest()


def ultimo_commit():
    """Hash e mensagem do commit HEAD atual; string vazia se git nao disponivel."""
    try:
        saida = subprocess.run(
            ["git", "log", "-1", "--format=%H | %s"],
            capture_output=True, text=True, check=True,
        )
        return saida.stdout.strip()
    except Exception:
        return "N/A"


def gerar():
    digests = ler_hashes_individuais(ENTRADA)
    master  = hash_master(digests)  # numero hexadecimal unico de 128 digitos (SHA-512)

    linhas = [
        "=" * 80,
        "  RESUMO DIGITAL HASH — PROJETO PLANTANDO VIDA",
        "=" * 80,
        f"  Data/Hora   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "  Algoritmo   : SHA-512",
        f"  Arquivos    : {len(digests)} arquivos verificados",
        f"  Ultimo commit: {ultimo_commit()}",
        "-" * 80,
        "  HASH MASTER (SHA-512 de todos os hashes combinados):",
        "",
        f"  {master}",
        "",
        "=" * 80,
        "  Este hash representa o estado integro do projeto neste momento.",
        "  Qualquer alteracao em qualquer arquivo modifica completamente este valor.",
        "=" * 80,
    ]

    with open(SAIDA, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas) + "\n")

    print(f"Arquivo gerado: {SAIDA}")
    print(f"  {len(digests)} arquivos combinados")
    print(f"  HASH MASTER: {master}")


if __name__ == "__main__":
    gerar()

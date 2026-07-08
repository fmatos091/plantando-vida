"""
Gera um manifesto de hashes SHA-256 do código-fonte do projeto, para uso
como evidência de anterioridade em registro de patente / programa de
computador (INPI ou similar).

Escopo: todos os arquivos .py, .html, .js e .css do projeto, exceto
pastas geradas/irrelevantes ao código (cache, ambiente virtual, uploads
de usuários, controle de versão).

Saída: HASH_CODIGO_FONTE.txt, contendo:
  - data/hora de geração
  - lista de cada arquivo com seu SHA-256 individual
  - hash final SHA-256, calculado sobre a concatenação ordenada de
    "caminho:hash" de todos os arquivos — qualquer alteração em
    qualquer arquivo muda esse hash final.

Reexecute este script sempre que quiser gerar uma nova "fotografia"
hash do código em outro momento (ex: antes de protocolar o registro).
"""
import hashlib
import os
from datetime import datetime, timezone

RAIZ = os.path.dirname(os.path.abspath(__file__))

# Extensões consideradas "código-fonte" para este manifesto
EXTENSOES = {".py", ".html", ".js", ".css"}

# Diretórios excluídos por não conterem código-fonte do projeto
DIRS_EXCLUIDOS = {
    ".git", "__pycache__", "venv", "env", ".venv",
    ".vscode", ".idea", ".claude", "node_modules",
    "uploads", "qrcodes",
}


def coletar_arquivos():
    """Percorre o projeto e retorna os caminhos relativos elegíveis, ordenados."""
    encontrados = []
    for atual, dirs, arquivos in os.walk(RAIZ):
        dirs[:] = [d for d in dirs if d not in DIRS_EXCLUIDOS]
        for nome in arquivos:
            if os.path.splitext(nome)[1].lower() in EXTENSOES:
                caminho_abs = os.path.join(atual, nome)
                caminho_rel = os.path.relpath(caminho_abs, RAIZ).replace("\\", "/")
                encontrados.append(caminho_rel)
    return sorted(encontrados)


def sha256_arquivo(caminho_rel):
    """Calcula o SHA-256 do conteúdo bruto (bytes) de um arquivo."""
    h = hashlib.sha256()
    with open(os.path.join(RAIZ, caminho_rel), "rb") as f:
        for bloco in iter(lambda: f.read(8192), b""):
            h.update(bloco)
    return h.hexdigest()


def main():
    arquivos = coletar_arquivos()
    linhas_manifesto = []
    hash_combinado = hashlib.sha256()

    for caminho_rel in arquivos:
        digest = sha256_arquivo(caminho_rel)
        linhas_manifesto.append(f"{digest}  {caminho_rel}")
        # Alimenta o hash final com "caminho:hash" — ordem determinística
        hash_combinado.update(f"{caminho_rel}:{digest}\n".encode("utf-8"))

    agora = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    saida = os.path.join(RAIZ, "HASH_CODIGO_FONTE.txt")

    with open(saida, "w", encoding="utf-8") as f:
        f.write("PROJETO PLANTANDO VIDA — MANIFESTO DE HASH DO CÓDIGO-FONTE\n")
        f.write("=" * 70 + "\n")
        f.write(f"Gerado em: {agora}\n")
        f.write(f"Total de arquivos: {len(arquivos)}\n")
        f.write(f"Algoritmo: SHA-256\n")
        f.write("=" * 70 + "\n\n")
        f.write("HASH FINAL (combinado de todos os arquivos abaixo):\n")
        f.write(f"{hash_combinado.hexdigest()}\n\n")
        f.write("=" * 70 + "\n")
        f.write("MANIFESTO INDIVIDUAL (sha256  caminho/do/arquivo)\n")
        f.write("=" * 70 + "\n")
        f.write("\n".join(linhas_manifesto) + "\n")

    print(f"Manifesto gerado: {saida}")
    print(f"Arquivos incluídos: {len(arquivos)}")
    print(f"HASH FINAL: {hash_combinado.hexdigest()}")


if __name__ == "__main__":
    main()

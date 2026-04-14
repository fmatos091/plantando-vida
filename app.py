import os
from flask import Flask, render_template

# Carrega variáveis do arquivo .env em desenvolvimento local.
# Em produção (Railway) as variáveis já estão no ambiente — load_dotenv não sobrescreve.
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)

# Secret key: lida do ambiente (.env local ou variável do Railway em produção).
app.secret_key = os.environ.get("SECRET_KEY", "sua_chave_super_secreta")



#app = Flask(__name__)


from views import *

if __name__ == "__main__":
    # Em produção o gunicorn ignora este bloco.
    # debug=False garante segurança ao rodar localmente com variável de ambiente.
    debug = os.environ.get("FLASK_ENV") != "production"
    app.run(debug=debug)


# ===================== BANCO DE DADOS =====================
# Usa PostgreSQL em produção (Railway, via DATABASE_URL) e SQLite localmente.
# A função get_db() de db.py abstrai a diferença de conexão e de placeholders.
from db import get_db, is_postgres

def init_db():
    conn   = get_db()
    cursor = conn.cursor()

    # Define os tipos de chave primária e timestamp conforme o banco ativo.
    # SQLite: INTEGER PRIMARY KEY AUTOINCREMENT + datetime('now','localtime')
    # PostgreSQL: SERIAL PRIMARY KEY + CURRENT_TIMESTAMP
    if is_postgres():
        pk = "SERIAL PRIMARY KEY"
        ts = "CURRENT_TIMESTAMP"
    else:
        pk = "INTEGER PRIMARY KEY AUTOINCREMENT"
        ts = "datetime('now','localtime')"

    # Tabela principal de usuários do sistema
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS usuarios (
        id              {pk},
        nome            TEXT NOT NULL,
        email           TEXT NOT NULL UNIQUE,
        senha           TEXT NOT NULL,
        cpf             TEXT,
        telefone        TEXT,
        data_nascimento TEXT
    )
    """)

    # Tabela de fornecedores de mudas
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS fornecedores (
        id           {pk},
        razao_social TEXT NOT NULL,
        cnpj         TEXT NOT NULL UNIQUE,
        whatsapp     TEXT NOT NULL,
        tipo_planta  TEXT NOT NULL,
        maps_link    TEXT,
        senha        TEXT,
        cidade       TEXT,
        uf           TEXT,
        ativo        INTEGER DEFAULT 1
    )
    """)

    # Tabela de plantios: registra cada aquisição vinculando usuário e fornecedor
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS plantios (
        id            {pk},
        usuario_id    INTEGER NOT NULL,
        fornecedor_id INTEGER NOT NULL,
        tipo          TEXT NOT NULL,
        status        TEXT DEFAULT 'pendente',
        data          TEXT DEFAULT ({ts})
    )
    """)

    # Tabela plantas_go: registro completo de cada plantio com localização e acompanhamentos
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS plantas_go (
        id               {pk},
        data_plantio     DATE    NOT NULL,
        responsavel_id   INTEGER NOT NULL,
        especie          TEXT    NOT NULL,
        municipio        TEXT    NOT NULL,
        bairro           TEXT    NOT NULL,
        latitude         REAL,
        longitude        REAL,
        acompanhamento_1 DATE,
        foto_1           TEXT,
        acompanhamento_2 DATE,
        foto_2           TEXT,
        acompanhamento_3 DATE,
        foto_3           TEXT,
        status           TEXT    DEFAULT 'em_analise',
        fornecedor_id    INTEGER,
        justificativa    TEXT,
        criado_em        TEXT    DEFAULT ({ts})
    )
    """)

    # Tabela de tokens para redefinição de senha
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS reset_tokens (
        id    {pk},
        email TEXT NOT NULL,
        token TEXT NOT NULL UNIQUE,
        usado INTEGER DEFAULT 0
    )
    """)

    # Tabela de espécies de plantas cadastradas pelo admin
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS especies_plantas (
        id        {pk},
        nome      TEXT NOT NULL,
        tipo      TEXT DEFAULT 'Nativa',
        valor     REAL DEFAULT 50.0,
        criado_em TEXT DEFAULT ({ts})
    )
    """)

    # Tabela de dados bancários da entidade responsável pelo projeto (registro único)
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS dados_bancarios (
        id               {pk},
        nome_empresarial TEXT,
        banco            TEXT,
        conta            TEXT,
        agencia          TEXT,
        chave_pix        TEXT,
        qrcode_pix       TEXT
    )
    """)

    # Tabela de compras de mudas: registra intenção de compra antes do plantio.
    # comprovante: nome do arquivo de pagamento salvo em static/uploads/.
    # status evolui: em_analise → aprovado | reprovado (ação do admin).
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS compras (
        id            {pk},
        usuario_id    INTEGER NOT NULL,
        fornecedor_id INTEGER,
        especie_nome  TEXT    NOT NULL,
        tipo_planta   TEXT    NOT NULL,
        valor         REAL    DEFAULT 0.0,
        comprovante   TEXT,
        status        TEXT    DEFAULT 'em_analise',
        criado_em     TEXT    DEFAULT ({ts})
    )
    """)

    # ---- Migrações seguras para bancos já existentes ----
    # Adiciona colunas que podem não existir em instalações anteriores.
    # SQLite: try/except (não suporta IF NOT EXISTS no ADD COLUMN antes da v3.37)
    # PostgreSQL: suporta ADD COLUMN IF NOT EXISTS nativamente
    migracoes = {
        "fornecedores": ["senha TEXT", "cidade TEXT", "uf TEXT", "ativo INTEGER DEFAULT 1", "email TEXT"],
        "usuarios":     ["cpf TEXT", "telefone TEXT", "data_nascimento TEXT"],
        "plantas_go":   [
            "status TEXT DEFAULT 'em_analise'",
            "fornecedor_id INTEGER",
            "justificativa TEXT",
        ],
        # data_validacao: preenchida pelo fornecedor ao escanear o voucher de retirada.
        # Quando preenchida, status da compra muda para 'retirado'.
        "compras": ["data_validacao TEXT"],
    }

    for tabela, colunas in migracoes.items():
        for coluna in colunas:
            try:
                if is_postgres():
                    # PostgreSQL suporta ADD COLUMN IF NOT EXISTS nativamente (v9.6+)
                    cursor.execute(
                        f"ALTER TABLE {tabela} ADD COLUMN IF NOT EXISTS {coluna}"
                    )
                else:
                    cursor.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna}")
            except Exception:
                pass  # Coluna já existe — ignorar

    conn.commit()
    conn.close()

init_db()


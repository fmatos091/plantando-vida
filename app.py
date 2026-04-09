import os
from flask import Flask, render_template

app = Flask(__name__)

# Secret key: em produção usa a variável de ambiente SECRET_KEY do Railway.
# Em desenvolvimento local usa o valor padrão abaixo.
app.secret_key = os.environ.get("SECRET_KEY", "sua_chave_super_secreta")



#app = Flask(__name__)


from views import *

if __name__ == "__main__":
    # Em produção o gunicorn ignora este bloco.
    # debug=False garante segurança ao rodar localmente com variável de ambiente.
    debug = os.environ.get("FLASK_ENV") != "production"
    app.run(debug=debug)


#Criando meu Banco de dados
import sqlite3

def init_db():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    # Tabela principal de usuários do sistema
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        senha TEXT NOT NULL
    )
    """)

    # Tabela de fornecedores de mudas.
    # Armazena empresas que desejam fornecer mudas nativas ou frutíferas ao projeto.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS fornecedores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        razao_social TEXT NOT NULL,
        cnpj TEXT NOT NULL UNIQUE,
        whatsapp TEXT NOT NULL,
        tipo_planta TEXT NOT NULL,
        maps_link TEXT,
        senha TEXT,
        cidade TEXT,
        uf TEXT
    )
    """)

    # Adiciona colunas novas caso a tabela já exista sem elas (migração segura).
    # SQLite ignora o erro se a coluna já existir.
    for coluna in ["senha TEXT", "cidade TEXT", "uf TEXT"]:
        try:
            cursor.execute(f"ALTER TABLE fornecedores ADD COLUMN {coluna}")
        except:
            pass

    # Migração segura: adiciona colunas de status, fornecedor e justificativa
    # em plantas_go caso a tabela já exista sem elas.
    for coluna in ["status TEXT DEFAULT 'em_analise'", "fornecedor_id INTEGER", "justificativa TEXT"]:
        try:
            cursor.execute(f"ALTER TABLE plantas_go ADD COLUMN {coluna}")
        except:
            pass

    # Tabela de plantios: registra cada aquisição de planta feita por um usuário
    # em um fornecedor credenciado. Status inicia como "pendente" até ser validado.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS plantios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario_id INTEGER NOT NULL,
        fornecedor_id INTEGER NOT NULL,
        tipo TEXT NOT NULL,
        status TEXT DEFAULT 'pendente',
        data TEXT DEFAULT (datetime('now','localtime'))
    )
    """)

    # Tabela plantas_go: armazena o registro completo de cada plantio realizado por um usuário.
    # Inclui dados de localização (município, bairro, latitude, longitude),
    # espécie plantada, e até 3 ciclos de acompanhamento com data e foto (caminho do arquivo).
    # responsavel_id referencia o id da tabela usuarios (cadastros).
    # As fotos são salvas em static/uploads/ e a coluna armazena apenas o nome do arquivo.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS plantas_go (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,

        -- Dados do plantio
        data_plantio        DATE    NOT NULL,
        responsavel_id      INTEGER NOT NULL REFERENCES usuarios(id),
        especie             TEXT    NOT NULL,
        municipio           TEXT    NOT NULL,
        bairro              TEXT    NOT NULL,

        -- Coordenadas geográficas (ex: -24.153268448091833, -49.81942797159125)
        latitude            REAL,
        longitude           REAL,

        -- Acompanhamento 1: data da visita e foto registrada
        acompanhamento_1    DATE,
        foto_1              TEXT,

        -- Acompanhamento 2: data da visita e foto registrada
        acompanhamento_2    DATE,
        foto_2              TEXT,

        -- Acompanhamento 3: data da visita e foto registrada
        acompanhamento_3    DATE,
        foto_3              TEXT,

        -- Data de criação do registro (preenchida automaticamente)
        criado_em           TEXT DEFAULT (datetime('now','localtime'))
    )
    """)

    # Tabela de tokens para redefinição de senha.
    # Cada token é gerado quando o usuário solicita "Esqueci minha senha"
    # e expira após o uso (coluna 'usado') ou por tempo (pode ser estendido futuramente).
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS reset_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT NOT NULL,
        token TEXT NOT NULL UNIQUE,
        usado INTEGER DEFAULT 0
    )
    """)

    conn.commit()
    conn.close()

init_db()


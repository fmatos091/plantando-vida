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


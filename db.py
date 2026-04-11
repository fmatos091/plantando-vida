"""
db.py — Camada de abstração do banco de dados
=============================================
Detecta automaticamente o ambiente:
  - LOCAL (sem DATABASE_URL):  usa SQLite (database.db)
  - PRODUÇÃO (com DATABASE_URL): usa PostgreSQL (Railway)

Uso em qualquer rota:
    from db import get_db
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tabela WHERE id = ?", (id,))
    ...
    conn.commit()
    conn.close()

O wrapper converte os placeholders '?' do SQLite para '%s' do psycopg2
automaticamente, sem precisar alterar as queries existentes.
"""

import os
import sqlite3

# URL de conexão injetada pelo Railway ao provisionar o PostgreSQL add-on.
# Localmente esta variável não existe, então DATABASE_URL será None.
DATABASE_URL = os.environ.get("DATABASE_URL")


def get_db():
    """Retorna uma conexão com o banco correto para o ambiente atual."""
    if DATABASE_URL:
        import psycopg2
        # Railway usa o prefixo 'postgres://' (legado); psycopg2 exige 'postgresql://'
        url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        conn = psycopg2.connect(url)
        return _PgConn(conn)
    # Desenvolvimento local: SQLite
    return sqlite3.connect("database.db")


def is_postgres():
    """Retorna True quando o app está rodando contra PostgreSQL (produção)."""
    return bool(DATABASE_URL)


# =============================================================================
# Wrappers internos — não precisam ser usados diretamente pelas rotas
# =============================================================================

class _PgConn:
    """
    Wrapper sobre psycopg2.connection que expõe a mesma interface do sqlite3.
    Encaminha commit() e close() diretamente; cursor() devolve _PgCursor.
    """
    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return _PgCursor(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


class _PgCursor:
    """
    Wrapper sobre psycopg2.cursor com duas responsabilidades:
      1. Substituir '?' por '%s' em todas as queries (compatibilidade SQLite → PG).
      2. Expor fetchone() e fetchall() diretamente.
    """
    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, params=()):
        # Converte placeholders SQLite → PostgreSQL
        sql = sql.replace("?", "%s")
        if params:
            self._cur.execute(sql, params)
        else:
            self._cur.execute(sql)

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self):
        return self._cur.rowcount

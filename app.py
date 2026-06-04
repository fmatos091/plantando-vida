import os
from flask import Flask, render_template, jsonify

# Carrega variáveis do arquivo .env em desenvolvimento local.
# Em produção (Railway) as variáveis já estão no ambiente — load_dotenv não sobrescreve.
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)

# Secret key: lida do ambiente (.env local ou variável do Railway em produção).
app.secret_key = os.environ.get("SECRET_KEY", "sua_chave_super_secreta")


# ===================== HELPER JINJA2: img_url =====================
# Converte nome de arquivo legado OU URL do Cloudinary para URL final exibível.
# Legado: apenas o nome (ex: "abc123.jpg") → montamos o caminho local.
# Cloudinary: URL completa (começa com "http") → retorna como está.
# pasta: subpasta local para arquivos legados ("uploads", "pix", "qrcodes").
@app.template_global()
def img_url(valor, pasta="uploads"):
    if not valor:
        return ""
    if valor.startswith("http"):
        return valor  # URL Cloudinary — usa diretamente
    return f"/static/{pasta}/{valor}"  # arquivo legado local


# ===================== HEALTH CHECK =====================
# Usado pelo Railway para verificar se o app está saudável após o deploy.
# Também usado pelo UptimeRobot para pings a cada 5 min (evita inatividade).
@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200


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

    # Membros da Administração: CPFs autorizados a adquirir em Local Credenciado.
    # dados_bancarios_id: FK para o registro de "Cad. Administração" do tenant (1:1).
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS membros_admin (
        id                 {pk},
        dados_bancarios_id INTEGER,
        nome               TEXT NOT NULL,
        cpf                TEXT NOT NULL,
        tenant_id          INTEGER DEFAULT 1,
        criado_em          TEXT DEFAULT ({ts})
    )
    """)

    # Tabela de dados bancários da Entidade Favorecida (registro único).
    # Mesma estrutura de dados_bancarios — exibida na aba de Fechamento Entidade.
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS dados_bancarios_entidade (
        id               {pk},
        nome_empresarial TEXT,
        banco            TEXT,
        conta            TEXT,
        agencia          TEXT,
        chave_pix        TEXT,
        qrcode_pix       TEXT
    )
    """)

    # Tabela de entidades favorecidas cadastradas (lista de APAEs ou similares).
    # Exibida e gerenciada na aba fechamento_entidade do painel admin.
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS entidades (
        id           {pk},
        razao_social TEXT NOT NULL,
        cnpj         TEXT,
        whatsapp     TEXT,
        criado_em    TEXT DEFAULT ({ts})
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

    # Tabela de faturamentos mensais por fornecedor (parte do fornecedor).
    # numero_baixa: código do período no formato MMAAAA (ex: "042026" = Abril/2026).
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS faturamentos (
        id               {pk},
        fornecedor_id    INTEGER NOT NULL,
        mes_ref          TEXT    NOT NULL,
        numero_baixa     TEXT    NOT NULL,
        quantidade       INTEGER NOT NULL DEFAULT 0,
        valor_bruto      REAL    NOT NULL DEFAULT 0.0,
        perc_fornecedor  REAL    NOT NULL DEFAULT 100.0,
        valor_liquido    REAL    NOT NULL DEFAULT 0.0,
        data_faturamento TEXT    DEFAULT ({ts})
    )
    """)

    # Tabela de faturamentos mensais da Entidade Favorecida.
    # Mesma estrutura de faturamentos, mas aplica perc_entidade de cada vigência.
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS faturamentos_entidade (
        id               {pk},
        fornecedor_id    INTEGER NOT NULL,
        mes_ref          TEXT    NOT NULL,
        numero_baixa     TEXT    NOT NULL,
        quantidade       INTEGER NOT NULL DEFAULT 0,
        valor_bruto      REAL    NOT NULL DEFAULT 0.0,
        perc_entidade    REAL    NOT NULL DEFAULT 0.0,
        valor_liquido    REAL    NOT NULL DEFAULT 0.0,
        data_faturamento TEXT    DEFAULT ({ts})
    )
    """)

    # Tabela de faturamentos mensais da Administração.
    # Mesma estrutura de faturamentos, mas aplica perc_admin de cada vigência.
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS faturamentos_admin (
        id               {pk},
        fornecedor_id    INTEGER NOT NULL,
        mes_ref          TEXT    NOT NULL,
        numero_baixa     TEXT    NOT NULL,
        quantidade       INTEGER NOT NULL DEFAULT 0,
        valor_bruto      REAL    NOT NULL DEFAULT 0.0,
        perc_admin       REAL    NOT NULL DEFAULT 0.0,
        valor_liquido    REAL    NOT NULL DEFAULT 0.0,
        data_faturamento TEXT    DEFAULT ({ts})
    )
    """)

    # Tabela de percentuais de distribuição do valor das plantas por vigência.
    # Cada registro define como o valor de uma compra é dividido entre
    # fornecedor, entidade favorecida e administração, a partir de inicio_vigencia.
    # O sistema aplica o registro vigente na data de cada compra (data mais recente <= data_compra).
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS percentuais_vigencia (
        id               {pk},
        inicio_vigencia  TEXT NOT NULL,
        perc_fornecedor  REAL NOT NULL DEFAULT 80.0,
        perc_entidade    REAL NOT NULL DEFAULT 10.0,
        perc_admin       REAL NOT NULL DEFAULT 10.0,
        criado_em        TEXT DEFAULT ({ts})
    )
    """)

    # Tabela de entidades educacionais parceiras (escolas, ONGs, creches, instituições).
    # Separada da tabela entidades (APAEs financeiras) — sem vínculo com o sistema de faturamento.
    # Vinculada a usuarios (entidade_educacional_id) e plantas_go (entidade_educacional_id).
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS entidades_educacionais (
        id           {pk},
        razao_social TEXT NOT NULL,
        cnpj         TEXT,
        whatsapp     TEXT,
        banco        TEXT,
        agencia      TEXT,
        conta        TEXT,
        chave_pix    TEXT,
        qrcode_pix   TEXT,
        ativa        INTEGER DEFAULT 1,
        criado_em    TEXT DEFAULT ({ts})
    )
    """)

    # Tabela de aceites de Termos de Uso / LGPD por usuário.
    # Cada login gera um novo registro com a versão vigente dos termos,
    # IP do acesso e timestamp — garantindo rastreabilidade para conformidade legal.
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS aceites_termos (
        id          {pk},
        usuario_id  INTEGER NOT NULL,
        versao      TEXT    NOT NULL,
        ip          TEXT,
        aceito_em   TEXT    DEFAULT ({ts})
    )
    """)

    # Acompanhamentos ilimitados por plantio (substitui os 3 campos fixos de plantas_go).
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS acompanhamentos (
        id         {pk},
        plantio_id INTEGER NOT NULL,
        data_acomp TEXT,
        foto       TEXT,
        criado_em  TEXT DEFAULT ({ts})
    )
    """)

    # Tabela de membros vinculados a uma Entidade Educacional.
    # Usada para controlar acesso à rota /plantio/escolar:
    # somente usuários cujo CPF esteja cadastrado aqui podem acessar o fluxo escolar.
    # entidade_educacional_id: FK para entidades_educacionais.
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS membros (
        id                      {pk},
        entidade_educacional_id INTEGER NOT NULL,
        nome                    TEXT    NOT NULL,
        cpf                     TEXT    NOT NULL,
        tenant_id               INTEGER DEFAULT 1,
        criado_em               TEXT    DEFAULT ({ts})
    )
    """)

    # ===================== TABELA TENANTS (MULTI-TENANT) =====================
    # Cada tenant representa um cliente/projeto independente do sistema.
    # O Super Admin cria tenants via /super-admin/painel.
    # login: credencial usada em /admin/login (campo "login" do formulário).
    # senha_hash: hash werkzeug — nunca armazenar senha em texto puro.
    # slug: identificador único amigável (ex: "goiania") para URLs e detecção.
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS tenants (
        id          {pk},
        nome        TEXT NOT NULL,
        slug        TEXT NOT NULL UNIQUE,
        login       TEXT NOT NULL UNIQUE,
        senha_hash  TEXT NOT NULL,
        ativo       INTEGER DEFAULT 1,
        criado_em   TEXT    DEFAULT ({ts})
    )
    """)

    # ---- Migrações seguras para bancos já existentes ----
    # Adiciona colunas que podem não existir em instalações anteriores.
    # SQLite: try/except (não suporta IF NOT EXISTS no ADD COLUMN antes da v3.37)
    # PostgreSQL: suporta ADD COLUMN IF NOT EXISTS nativamente
    # tenant_id DEFAULT 1: todos os registros existentes pertencem ao tenant padrão (id=1).
    migracoes = {
        # tenant_id: chave estrangeira para isolamento multi-tenant.
        # Demais colunas: expansões anteriores do schema.
        "fornecedores": [
            "senha TEXT", "cidade TEXT", "uf TEXT", "ativo INTEGER DEFAULT 1", "email TEXT",
            "tenant_id INTEGER DEFAULT 1",
        ],
        # uf/cidade: localização do usuário.
        # entidade_educacional_id: vínculo opcional com entidade educacional parceira.
        "usuarios": [
            "cpf TEXT", "telefone TEXT", "data_nascimento TEXT",
            "uf TEXT", "cidade TEXT",
            "entidade_educacional_id INTEGER",
            "tenant_id INTEGER DEFAULT 1",
        ],
        # tenant_id: isolamento de plantios por projeto/cliente.
        "plantios": [
            "tenant_id INTEGER DEFAULT 1",
        ],
        # status/fornecedor_id/justificativa: gestão de aprovação de plantios pelo admin.
        # foto_plantio: foto tirada pelo usuário ao lado da cova antes de plantar (Etapa 3 do fluxo).
        # entidade_educacional_id: vínculo opcional com entidade educacional parceira.
        "plantas_go": [
            "status TEXT DEFAULT 'em_analise'",
            "fornecedor_id INTEGER",
            "justificativa TEXT",
            "foto_plantio TEXT",
            "entidade_educacional_id INTEGER",
            "tenant_id INTEGER DEFAULT 1",
        ],
        # data_validacao: preenchida pelo fornecedor ao escanear o voucher de retirada.
        # plantio_id: referência ao registro de plantas_go criado após o plantio definitivo.
        # faturamento_id: id do fechamento do fornecedor que incluiu esta compra.
        # faturamento_entidade_id: id do fechamento da entidade favorecida.
        # faturamento_admin_id: id do fechamento da administração.
        "compras": [
            "data_validacao TEXT",
            "plantio_id INTEGER",
            "faturamento_id INTEGER",
            "faturamento_entidade_id INTEGER",
            "faturamento_admin_id INTEGER",
            # Vincula doações escolares à entidade educacional escolhida pelo usuário
            "entidade_educacional_id INTEGER",
            "tenant_id INTEGER DEFAULT 1",
        ],
        # banco/conta/agencia/chave_pix/qrcode_pix: dados bancários da entidade (unificados ao cadastro).
        # ativa: 1 = entidade ativa no projeto (exibida para seleção no fechamento); 0 = inativa.
        "entidades": [
            "banco TEXT",
            "conta TEXT",
            "agencia TEXT",
            "chave_pix TEXT",
            "qrcode_pix TEXT",
            "ativa INTEGER DEFAULT 0",
            "tenant_id INTEGER DEFAULT 1",
        ],
        # entidade_id: referência à entidade favorecida vinculada neste fechamento.
        "faturamentos_entidade": [
            "entidade_id INTEGER",
            "tenant_id INTEGER DEFAULT 1",
        ],
        # Tabelas que só recebem tenant_id (sem outras colunas novas nesta migração)
        "entidades_educacionais":  ["tenant_id INTEGER DEFAULT 1"],
        # entidade_educacional_id: quando valor=0 (doação), vincula a espécie à entidade educacional
        "especies_plantas":        ["tenant_id INTEGER DEFAULT 1", "entidade_educacional_id INTEGER"],
        "dados_bancarios":         ["tenant_id INTEGER DEFAULT 1"],
        "dados_bancarios_entidade":["tenant_id INTEGER DEFAULT 1"],
        "faturamentos":            ["tenant_id INTEGER DEFAULT 1"],
        "faturamentos_admin":      ["tenant_id INTEGER DEFAULT 1"],
        "percentuais_vigencia":    ["tenant_id INTEGER DEFAULT 1"],
        "reset_tokens":            ["tenant_id INTEGER DEFAULT 1"],
        "aceites_termos":          ["tenant_id INTEGER DEFAULT 1"],
        # membros: tabela nova — migração cobre instâncias já existentes
        "membros":                 ["tenant_id INTEGER DEFAULT 1"],
        # acompanhamentos: tabela nova — tenant_id para isolamento por projeto
        "acompanhamentos":         ["tenant_id INTEGER DEFAULT 1"],
        # tipo: diferencia plantios credenciado / escolar / voluntario
        # tipo_planta: Nativa ou Frutífera (informado pelo voluntário na Etapa 5)
        "plantas_go":              ["tipo TEXT", "tipo_planta TEXT"],
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

    # ===================== COMMIT DDL =====================
    # Comita criação de tabelas e migrações ANTES do INSERT a seguir.
    # Isso é crítico no PostgreSQL (psycopg2): se o INSERT falhar (UNIQUE violation),
    # psycopg2 coloca a conexão em estado de erro e o commit subsequente lança
    # InFailedSqlTransaction. Comitando o DDL antes isolamos o risco do INSERT.
    conn.commit()

    # ===================== CRIAR TENANT PADRÃO =====================
    # Executado na primeira inicialização (INSERT ignorado se slug já existir).
    # Converte as credenciais ADMIN_LOGIN/ADMIN_SENHA do .env no tenant id=1.
    # Após a migração, o login em /admin/login consulta a tabela tenants.
    _adm_login = os.environ.get("ADMIN_LOGIN", "")
    _adm_senha = os.environ.get("ADMIN_SENHA", "")
    if _adm_login and _adm_senha:
        from werkzeug.security import generate_password_hash as _gph
        try:
            _hash = _gph(_adm_senha)
            cursor.execute(
                "INSERT INTO tenants (nome, slug, login, senha_hash, ativo) VALUES (?, ?, ?, ?, 1)",
                ("Projeto Padrão", "padrao", _adm_login, _hash)
            )
            conn.commit()  # Comita o INSERT do tenant padrão
        except Exception:
            # Tenant já existe (violação UNIQUE em slug/login).
            # Em PostgreSQL, um INSERT que falha coloca a conexão em estado de erro —
            # é obrigatório chamar rollback() antes de qualquer outra operação ou close().
            try:
                conn.rollback()
            except Exception:
                pass

    conn.close()

init_db()


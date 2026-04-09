import sqlite3
import secrets
import os
import uuid
from flask import render_template, request, redirect, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# Pasta de destino dos uploads de fotos de plantio
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "static", "uploads")
EXTENSOES_PERMITIDAS = {"jpg", "jpeg", "png"}

from app import app


# ===================== ROTA HOME =====================
# Página inicial do sistema com apresentação e botões de acesso.
@app.route('/')
def home():
    return render_template('index.html')


# ===================== ROTA DE CADASTRO =====================
# GET:  exibe o formulário de cadastro.
# POST: recebe os dados, salva o usuário no banco com senha hasheada
#       e redireciona para /login após sucesso.
@app.route("/cadastros", methods=["GET", "POST"])
def cadastro():
    if request.method == "POST":
        nome  = request.form["nome"]
        email = request.form["email"]
        senha = generate_password_hash(request.form["senha"])

        conn   = sqlite3.connect("database.db")
        cursor = conn.cursor()

        try:
            cursor.execute(
                "INSERT INTO usuarios (nome, email, senha) VALUES (?, ?, ?)",
                (nome, email, senha)
            )
            conn.commit()
        except:
            # Email já cadastrado — retorna mensagem de erro via flash
            flash("Este email já está cadastrado. Tente outro.", "erro")
            return redirect("/cadastros")
        finally:
            conn.close()

        return redirect("/login")

    return render_template("cadastros.html")


# ===================== ROTA DE LOGIN =====================
# GET:  exibe o formulário de login.
# POST: valida email e senha no banco.
#       Se correto: cria sessão e redireciona para /dashboard.
#       Se incorreto: exibe mensagem de erro via flash sem expor qual campo está errado.
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        senha = request.form["senha"]

        conn   = sqlite3.connect("database.db")
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM usuarios WHERE email = ?", (email,))
        usuario = cursor.fetchone()
        conn.close()

        # Verifica se o usuário existe e se a senha confere com o hash no banco
        if usuario and check_password_hash(usuario[3], senha):
            session["usuario_id"]   = usuario[0]
            session["usuario_nome"] = usuario[1]
            return redirect("/dashboard")

        # Credenciais inválidas: mensagem genérica por segurança
        flash("Email ou senha incorretos. Tente novamente.", "erro")
        return redirect("/login")

    return render_template("login.html")


# ===================== ROTA DO DASHBOARD =====================
# Exibe o painel do usuário após login.
# Se o usuário não estiver logado (sem sessão), redireciona para /login.
@app.route("/dashboard")
def dashboard():
    if "usuario_id" not in session:
        return redirect("/login")

    conn   = sqlite3.connect("database.db")
    cursor = conn.cursor()

    # Conta quantos plantios do usuário foram aprovados pelo admin.
    # Usado no dashboard para exibir o Card 2 apenas quando há plantas aprovadas.
    cursor.execute(
        "SELECT COUNT(*) FROM plantas_go WHERE responsavel_id = ? AND status = 'aprovado'",
        (session["usuario_id"],)
    )
    total_aprovados = cursor.fetchone()[0]
    conn.close()

    return render_template("dashboard.html",
                           nome=session["usuario_nome"],
                           total_aprovados=total_aprovados)


# ===================== ROTA DE LOGOUT =====================
# Limpa todos os dados da sessão e redireciona para /login.
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ===================== HELPER: SALVAR FOTO =====================
# Valida a extensão do arquivo enviado e salva em static/uploads/
# com nome único (uuid) para evitar conflitos.
# Retorna o nome do arquivo salvo ou None se inválido/vazio.
def salvar_foto(campo_arquivo):
    arquivo = request.files.get(campo_arquivo)
    if not arquivo or arquivo.filename == "":
        return None
    ext = arquivo.filename.rsplit(".", 1)[-1].lower()
    if ext not in EXTENSOES_PERMITIDAS:
        return None
    nome = f"{uuid.uuid4().hex}.{ext}"
    arquivo.save(os.path.join(UPLOAD_FOLDER, nome))
    return nome


# ===================== ROTA PLANTIO EM LOCAL CREDENCIADO =====================
# GET:  busca todos os fornecedores e exibe o formulário completo de registro.
#       Acesso restrito a usuários logados (session["usuario_id"]).
# POST: salva os dados do plantio na tabela plantas_go (incluindo fotos)
#       e registra a transação na tabela plantios com status "pendente".
@app.route("/plantio/credenciado", methods=["GET", "POST"])
def plantio_credenciado():
    if "usuario_id" not in session:
        return redirect("/login")

    if request.method == "POST":
        fornecedor_id = request.form.get("fornecedor_id")

        if not fornecedor_id:
            flash("Selecione um fornecedor credenciado antes de registrar.", "erro")
            return redirect("/plantio/credenciado")

        # Dados principais do plantio
        data_plantio = request.form.get("data_plantio")
        especie      = request.form.get("especie")
        municipio    = request.form.get("municipio")
        bairro       = request.form.get("bairro")
        latitude     = request.form.get("latitude") or None
        longitude    = request.form.get("longitude") or None

        # Dados dos acompanhamentos (opcionais)
        acomp_1 = request.form.get("acompanhamento_1") or None
        acomp_2 = request.form.get("acompanhamento_2") or None
        acomp_3 = request.form.get("acompanhamento_3") or None

        # Salva as fotos enviadas e obtém os nomes dos arquivos
        foto_1 = salvar_foto("foto_1")
        foto_2 = salvar_foto("foto_2")
        foto_3 = salvar_foto("foto_3")

        conn   = sqlite3.connect("database.db")
        cursor = conn.cursor()

        # Insere o registro completo na tabela plantas_go.
        # fornecedor_id e status='em_analise' são gravados para rastreamento e aprovação.
        cursor.execute("""
            INSERT INTO plantas_go (
                data_plantio, responsavel_id, especie, municipio, bairro,
                latitude, longitude,
                acompanhamento_1, foto_1,
                acompanhamento_2, foto_2,
                acompanhamento_3, foto_3,
                fornecedor_id, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data_plantio, session["usuario_id"], especie, municipio, bairro,
            latitude, longitude,
            acomp_1, foto_1,
            acomp_2, foto_2,
            acomp_3, foto_3,
            int(fornecedor_id), "em_analise"
        ))

        # Registra a transação na tabela plantios vinculando usuário e fornecedor
        cursor.execute(
            "INSERT INTO plantios (usuario_id, fornecedor_id, tipo, status) VALUES (?, ?, ?, ?)",
            (session["usuario_id"], int(fornecedor_id), "credenciado", "pendente")
        )

        conn.commit()
        conn.close()

        flash("Plantio registrado com sucesso! Aguardando aprovação.", "sucesso")
        return redirect("/dashboard")

    # GET: busca todos os fornecedores para listar na página
    conn   = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, razao_social, cidade, uf, tipo_planta, whatsapp, maps_link FROM fornecedores ORDER BY uf, cidade")
    fornecedores = cursor.fetchall()
    conn.close()

    return render_template("plantio_credenciado.html", fornecedores=fornecedores)


# ===================== ROTA MEUS PLANTIOS (PENDENTES / TODOS) =====================
# Exibe todos os plantios do usuário logado com seus respectivos status.
# Acesso restrito a usuários autenticados.
@app.route("/plantios/pendentes")
def plantios_pendentes():
    if "usuario_id" not in session:
        return redirect("/login")

    conn   = sqlite3.connect("database.db")
    cursor = conn.cursor()

    # Busca apenas plantios em análise ou reprovados do usuário.
    # Plantios aprovados são excluídos desta view — aparecem somente em /plantios/aprovados.
    cursor.execute("""
        SELECT pg.id, pg.data_plantio, pg.especie, pg.municipio, pg.bairro,
               pg.status, pg.justificativa, pg.criado_em,
               COALESCE(f.razao_social, 'Fornecedor não informado') AS fornecedor_nome
        FROM plantas_go pg
        LEFT JOIN fornecedores f ON f.id = pg.fornecedor_id
        WHERE pg.responsavel_id = ? AND pg.status != 'aprovado'
        ORDER BY pg.criado_em DESC
    """, (session["usuario_id"],))
    plantios = cursor.fetchall()
    conn.close()

    return render_template("plantios_pendentes.html", plantios=plantios)


# ===================== ROTA PLANTIOS APROVADOS =====================
# Exibe somente os plantios com status 'aprovado' do usuário logado.
# Acesso restrito a usuários autenticados (session["usuario_id"]).
@app.route("/plantios/aprovados")
def plantios_aprovados():
    if "usuario_id" not in session:
        return redirect("/login")

    conn   = sqlite3.connect("database.db")
    cursor = conn.cursor()

    # Busca apenas os plantios aprovados pelo administrador
    cursor.execute("""
        SELECT pg.id, pg.data_plantio, pg.especie, pg.municipio, pg.bairro,
               pg.criado_em,
               COALESCE(f.razao_social, 'Fornecedor não informado') AS fornecedor_nome
        FROM plantas_go pg
        LEFT JOIN fornecedores f ON f.id = pg.fornecedor_id
        WHERE pg.responsavel_id = ? AND pg.status = 'aprovado'
        ORDER BY pg.criado_em DESC
    """, (session["usuario_id"],))
    plantios = cursor.fetchall()
    conn.close()

    return render_template("plantios_aprovados.html", plantios=plantios)


# ===================== ROTA ADMIN: APROVAR PLANTIO =====================
# Altera o status do plantio para 'aprovado'.
# Acesso restrito ao administrador (session["admin"]).
@app.route("/admin/plantio/<int:plantio_id>/aprovar", methods=["POST"])
def admin_aprovar_plantio(plantio_id):
    if not session.get("admin"):
        return redirect("/admin/login")

    conn   = sqlite3.connect("database.db")
    cursor = conn.cursor()

    # Atualiza status para aprovado e limpa qualquer justificativa anterior
    cursor.execute(
        "UPDATE plantas_go SET status = 'aprovado', justificativa = NULL WHERE id = ?",
        (plantio_id,)
    )
    conn.commit()
    conn.close()

    flash("Plantio aprovado com sucesso.", "sucesso")
    return redirect("/admin/painel?tipo=plantios")


# ===================== ROTA ADMIN: REPROVAR PLANTIO =====================
# Altera o status do plantio para 'reprovado' com justificativa obrigatória (máx. 100 chars).
# Acesso restrito ao administrador (session["admin"]).
@app.route("/admin/plantio/<int:plantio_id>/reprovar", methods=["POST"])
def admin_reprovar_plantio(plantio_id):
    if not session.get("admin"):
        return redirect("/admin/login")

    justificativa = request.form.get("justificativa", "").strip()

    # Valida que a justificativa foi informada e respeita o limite de 100 caracteres
    if not justificativa:
        flash("Informe a justificativa para reprovar o plantio.", "erro")
        return redirect("/admin/painel?tipo=plantios")

    if len(justificativa) > 100:
        flash("A justificativa deve ter no máximo 100 caracteres.", "erro")
        return redirect("/admin/painel?tipo=plantios")

    conn   = sqlite3.connect("database.db")
    cursor = conn.cursor()

    # Atualiza status para reprovado e grava a justificativa
    cursor.execute(
        "UPDATE plantas_go SET status = 'reprovado', justificativa = ? WHERE id = ?",
        (justificativa, plantio_id)
    )
    conn.commit()
    conn.close()

    flash("Plantio reprovado.", "sucesso")
    return redirect("/admin/painel?tipo=plantios")


# ===================== ROTA ESQUECI MINHA SENHA =====================
# GET:  exibe o formulário para o usuário informar o email cadastrado.
# POST: verifica se o email existe no banco.
#       Se sim: gera um token único, salva na tabela reset_tokens
#               e exibe o link de redefinição (em produção seria enviado por email).
#       Se não: exibe a mesma mensagem neutra por segurança (não confirma se email existe).
@app.route("/esqueci-senha", methods=["GET", "POST"])
def esqueci_senha():
    if request.method == "POST":
        email = request.form["email"]

        conn   = sqlite3.connect("database.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM usuarios WHERE email = ?", (email,))
        usuario = cursor.fetchone()

        if usuario:
            # Gera token seguro e único para este pedido de redefinição
            token = secrets.token_urlsafe(32)
            cursor.execute(
                "INSERT INTO reset_tokens (email, token, usado) VALUES (?, ?, 0)",
                (email, token)
            )
            conn.commit()
            conn.close()

            # Monta o link de redefinição e exibe na tela
            # Em produção: substituir por envio de email via Flask-Mail
            link = f"http://127.0.0.1:5000/redefinir-senha/{token}"
            return render_template("esqueci_senha.html", link=link, enviado=True)

        conn.close()
        # Email não encontrado: mesma tela, mesma mensagem (segurança)
        return render_template("esqueci_senha.html", enviado=True)

    return render_template("esqueci_senha.html", enviado=False)


# ===================== ROTA REDEFINIR SENHA =====================
# GET:  valida o token na URL. Se válido e não usado, exibe o formulário
#       para o usuário digitar a nova senha.
# POST: recebe a nova senha, aplica o hash e atualiza no banco.
#       Marca o token como usado (coluna 'usado' = 1) para evitar reutilização.
@app.route("/redefinir-senha/<token>", methods=["GET", "POST"])
def redefinir_senha(token):
    conn   = sqlite3.connect("database.db")
    cursor = conn.cursor()

    # Busca o token no banco — deve existir e não ter sido usado ainda
    cursor.execute(
        "SELECT email FROM reset_tokens WHERE token = ? AND usado = 0",
        (token,)
    )
    resultado = cursor.fetchone()

    if not resultado:
        conn.close()
        # Token inválido ou já utilizado
        flash("Este link é inválido ou já foi utilizado.", "erro")
        return redirect("/login")

    if request.method == "POST":
        nova_senha = generate_password_hash(request.form["senha"])
        email      = resultado[0]

        # Atualiza a senha do usuário no banco
        cursor.execute(
            "UPDATE usuarios SET senha = ? WHERE email = ?",
            (nova_senha, email)
        )

        # Marca o token como usado para impedir reutilização
        cursor.execute(
            "UPDATE reset_tokens SET usado = 1 WHERE token = ?",
            (token,)
        )
        conn.commit()
        conn.close()

        flash("Senha redefinida com sucesso! Faça seu login.", "sucesso")
        return redirect("/login")

    conn.close()
    return render_template("redefinir_senha.html", token=token)


# ===================== ROTA CADASTRO DE FORNECEDOR =====================
# GET:  exibe o formulário de cadastro de fornecedor com mensagem de apresentação.
# POST: recebe os dados do formulário, valida e salva no banco na tabela fornecedores.
#       Após salvar, redireciona para a lista de fornecedores.
@app.route("/fornecedor", methods=["GET", "POST"])
def fornecedor():
    if request.method == "POST":
        razao_social = request.form["razao_social"]
        cnpj         = request.form["cnpj"]
        whatsapp     = request.form["whatsapp"]
        cidade       = request.form.get("cidade", "")
        uf           = request.form.get("uf", "").upper()
        maps_link    = request.form.get("maps_link", "")
        tipo_planta  = request.form.get("tipo_planta", "")

        # Aplica hash na senha antes de salvar no banco
        senha = generate_password_hash(request.form["senha"])

        conn   = sqlite3.connect("database.db")
        cursor = conn.cursor()

        try:
            cursor.execute(
                "INSERT INTO fornecedores (razao_social, cnpj, whatsapp, tipo_planta, maps_link, senha, cidade, uf) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (razao_social, cnpj, whatsapp, tipo_planta, maps_link, senha, cidade, uf)
            )
            conn.commit()
            flash("Fornecedor cadastrado com sucesso!", "sucesso")
        except:
            # CNPJ já cadastrado no banco
            flash("Este CNPJ já está cadastrado.", "erro")
        finally:
            conn.close()

        return redirect("/fornecedores")

    return render_template("fornecedor.html")


# ===================== ROTA LISTA DE FORNECEDORES =====================
# Exibe todos os fornecedores cadastrados no banco com opções de editar e excluir.
@app.route("/fornecedores")
def listar_fornecedores():
    conn   = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM fornecedores")
    fornecedores = cursor.fetchall()
    conn.close()
    return render_template("fornecedores_lista.html", fornecedores=fornecedores)


# ===================== ROTA EDITAR FORNECEDOR =====================
# GET:  carrega os dados do fornecedor pelo id e exibe o formulário preenchido.
# POST: recebe os novos dados e atualiza o registro no banco.
@app.route("/fornecedor/<int:id>/editar", methods=["GET", "POST"])
def editar_fornecedor(id):
    conn   = sqlite3.connect("database.db")
    cursor = conn.cursor()

    if request.method == "POST":
        razao_social = request.form["razao_social"]
        cnpj         = request.form["cnpj"]
        whatsapp     = request.form["whatsapp"]
        tipo_planta  = request.form.get("tipo_planta", "")
        maps_link    = request.form.get("maps_link", "")

        # Atualiza todos os campos do fornecedor pelo id
        cursor.execute("""
            UPDATE fornecedores
            SET razao_social=?, cnpj=?, whatsapp=?, tipo_planta=?, maps_link=?
            WHERE id=?
        """, (razao_social, cnpj, whatsapp, tipo_planta, maps_link, id))
        conn.commit()
        conn.close()

        flash("Fornecedor atualizado com sucesso!", "sucesso")
        return redirect("/fornecedores")

    # Busca o fornecedor pelo id para preencher o formulário
    cursor.execute("SELECT * FROM fornecedores WHERE id = ?", (id,))
    fornecedor = cursor.fetchone()
    conn.close()
    return render_template("fornecedor_editar.html", fornecedor=fornecedor)


# ===================== ROTA EXCLUIR FORNECEDOR =====================
# Remove o fornecedor do banco pelo id e redireciona para a lista.
@app.route("/fornecedor/<int:id>/excluir", methods=["POST"])
def excluir_fornecedor(id):
    conn   = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM fornecedores WHERE id = ?", (id,))
    conn.commit()
    conn.close()

    flash("Fornecedor removido com sucesso.", "sucesso")
    return redirect("/fornecedores")


# ===================== ROTA LOGIN DO FORNECEDOR =====================
# GET:  exibe o formulário de acesso com campo CNPJ.
# POST: busca o CNPJ no banco.
#       Se encontrado: salva o id na sessão (session["fornecedor_id"]) e redireciona
#       para o painel exclusivo do fornecedor.
#       Se não encontrado: exibe mensagem de erro via flash.
@app.route("/fornecedor/login", methods=["GET", "POST"])
def fornecedor_login():
    if request.method == "POST":
        cnpj  = request.form["cnpj"].replace(".", "").replace("/", "").replace("-", "").strip()
        senha = request.form["senha"]

        conn   = sqlite3.connect("database.db")
        cursor = conn.cursor()

        # Busca o fornecedor pelo CNPJ — remove formatação para comparar apenas dígitos
        cursor.execute(
            "SELECT id, razao_social, senha FROM fornecedores WHERE REPLACE(REPLACE(REPLACE(cnpj,'.',''),'/',''),'-','') = ?",
            (cnpj,)
        )
        fornecedor = cursor.fetchone()
        conn.close()

        # Valida CNPJ e senha com check_password_hash
        if fornecedor and fornecedor[2] and check_password_hash(fornecedor[2], senha):
            session["fornecedor_id"]   = fornecedor[0]
            session["fornecedor_nome"] = fornecedor[1]
            return redirect("/fornecedor/painel")

        # Credenciais inválidas: mensagem genérica por segurança
        flash("CNPJ ou senha incorretos. Verifique ou faça seu cadastro.", "erro")
        return redirect("/fornecedor/login")

    return render_template("fornecedor_login.html")


# ===================== ROTA PAINEL DO FORNECEDOR =====================
# Exibe e permite editar os dados do próprio fornecedor logado.
# GET:  carrega os dados do fornecedor pelo id gravado na sessão.
# POST: atualiza os dados usando SEMPRE o id da sessão (nunca da URL),
#       garantindo que o fornecedor só altere o próprio cadastro.
@app.route("/fornecedor/painel", methods=["GET", "POST"])
def fornecedor_painel():
    # Verifica se o fornecedor está autenticado na sessão
    if "fornecedor_id" not in session:
        flash("Acesse com seu CNPJ para continuar.", "erro")
        return redirect("/fornecedor/login")

    fornecedor_id = session["fornecedor_id"]
    conn   = sqlite3.connect("database.db")
    cursor = conn.cursor()

    if request.method == "POST":
        razao_social = request.form["razao_social"]
        cnpj         = request.form["cnpj"]
        whatsapp     = request.form["whatsapp"]
        cidade       = request.form.get("cidade", "")
        uf           = request.form.get("uf", "").upper()
        tipo_planta  = request.form.get("tipo_planta", "")
        maps_link    = request.form.get("maps_link", "")

        # Atualiza usando o id da sessão — impede alteração de outros cadastros
        cursor.execute("""
            UPDATE fornecedores
            SET razao_social=?, cnpj=?, whatsapp=?, tipo_planta=?, maps_link=?, cidade=?, uf=?
            WHERE id=?
        """, (razao_social, cnpj, whatsapp, tipo_planta, maps_link, cidade, uf, fornecedor_id))
        conn.commit()

        # Atualiza o nome na sessão caso tenha sido alterado
        session["fornecedor_nome"] = razao_social
        conn.close()

        flash("Cadastro atualizado com sucesso!", "sucesso")
        return redirect("/fornecedor/painel")

    # Busca os dados atuais do fornecedor pelo id da sessão
    cursor.execute("SELECT * FROM fornecedores WHERE id = ?", (fornecedor_id,))
    fornecedor = cursor.fetchone()
    conn.close()

    return render_template("fornecedor_painel.html", fornecedor=fornecedor)


# ===================== ROTA EXCLUIR PRÓPRIO CADASTRO =====================
# Remove o cadastro do fornecedor usando o id da sessão (nunca da URL).
# Após excluir, limpa a sessão e redireciona para a página de cadastro.
@app.route("/fornecedor/painel/excluir", methods=["POST"])
def fornecedor_excluir_proprio():
    if "fornecedor_id" not in session:
        return redirect("/fornecedor/login")

    fornecedor_id = session["fornecedor_id"]
    conn   = sqlite3.connect("database.db")
    cursor = conn.cursor()

    # Exclui apenas o registro do próprio fornecedor logado
    cursor.execute("DELETE FROM fornecedores WHERE id = ?", (fornecedor_id,))
    conn.commit()
    conn.close()

    # Limpa a sessão do fornecedor após exclusão
    session.pop("fornecedor_id", None)
    session.pop("fornecedor_nome", None)

    flash("Seu cadastro foi removido com sucesso.", "sucesso")
    return redirect("/fornecedor")


# ===================== ROTA LOGOUT DO FORNECEDOR =====================
# Encerra a sessão do fornecedor e redireciona para a página de acesso.
@app.route("/fornecedor/logout")
def fornecedor_logout():
    session.pop("fornecedor_id", None)
    session.pop("fornecedor_nome", None)
    return redirect("/fornecedor/login")


# ===================== ROTA ESQUECI MINHA SENHA — FORNECEDOR =====================
# GET:  exibe o formulário onde o fornecedor informa o CNPJ cadastrado.
# POST (passo 1): valida o CNPJ no banco e, se encontrado, exibe os campos
#                 para digitar e confirmar a nova senha.
# POST (passo 2): recebe a nova senha, aplica hash e atualiza no banco.
#                 Redireciona para /fornecedor/login com mensagem de sucesso.
@app.route("/fornecedor/esqueci-senha", methods=["GET", "POST"])
def fornecedor_esqueci_senha():
    if request.method == "POST":
        etapa = request.form.get("etapa", "1")

        if etapa == "1":
            # Etapa 1: fornecedor informa o CNPJ para localizar o cadastro
            cnpj = request.form["cnpj"].replace(".", "").replace("/", "").replace("-", "").strip()

            conn   = sqlite3.connect("database.db")
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, razao_social FROM fornecedores WHERE REPLACE(REPLACE(REPLACE(cnpj,'.',''),'/',''),'-','') = ?",
                (cnpj,)
            )
            fornecedor = cursor.fetchone()
            conn.close()

            if fornecedor:
                # CNPJ encontrado: exibe os campos para criar nova senha
                return render_template("fornecedor_esqueci_senha.html",
                                       etapa=2,
                                       cnpj_limpo=cnpj,
                                       razao_social=fornecedor[1])

            # CNPJ não encontrado: mensagem neutra por segurança
            flash("CNPJ não encontrado. Verifique ou faça seu cadastro.", "erro")
            return render_template("fornecedor_esqueci_senha.html", etapa=1)

        elif etapa == "2":
            # Etapa 2: fornecedor define a nova senha
            cnpj_limpo = request.form["cnpj_limpo"]
            nova_senha  = generate_password_hash(request.form["nova_senha"])

            conn   = sqlite3.connect("database.db")
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE fornecedores SET senha=? WHERE REPLACE(REPLACE(REPLACE(cnpj,'.',''),'/',''),'-','') = ?",
                (nova_senha, cnpj_limpo)
            )
            conn.commit()
            conn.close()

            flash("Senha redefinida com sucesso! Faça seu login.", "sucesso")
            return redirect("/fornecedor/login")

    # GET: exibe a etapa 1 (formulário para informar o CNPJ)
    return render_template("fornecedor_esqueci_senha.html", etapa=1)


# ===================== CREDENCIAIS DO ADMINISTRADOR =====================
# Login e senha fixos definidos no código conforme solicitado.
# Em produção recomenda-se usar variáveis de ambiente para maior segurança.
ADMIN_LOGIN = "FESA202410"
ADMIN_SENHA = "28122024"


# ===================== ROTA LOGIN ADMINISTRADOR =====================
# GET:  exibe o formulário de login do administrador.
# POST: valida login e senha fixos.
#       Se corretos: cria session["admin"] e redireciona para o painel.
#       Se incorretos: exibe mensagem de erro via flash.
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        login = request.form["login"]
        senha = request.form["senha"]

        if login == ADMIN_LOGIN and senha == ADMIN_SENHA:
            # Credenciais corretas: marca sessão de administrador
            session["admin"] = True
            return redirect("/admin/painel")

        # Credenciais incorretas
        flash("Login ou senha incorretos.", "erro")
        return redirect("/admin/login")

    return render_template("admin_login.html")


# ===================== ROTA PAINEL ADMINISTRADOR =====================
# Exibe todos os fornecedores e cadastros com filtro de busca por texto.
# O filtro é aplicado via query string (?busca=termo&tipo=fornecedores|usuarios).
# Acesso bloqueado se session["admin"] não estiver ativo.
@app.route("/admin/painel")
def admin_painel():
    if not session.get("admin"):
        flash("Acesso restrito. Faça o login.", "erro")
        return redirect("/admin/login")

    conn   = sqlite3.connect("database.db")
    cursor = conn.cursor()

    # Parâmetros de filtro vindos da URL (query string)
    busca = request.args.get("busca", "").strip()
    tipo  = request.args.get("tipo", "fornecedores")  # aba ativa padrão

    # ---- Consulta Fornecedores com filtro por razão social, CNPJ ou cidade ----
    if busca:
        cursor.execute("""
            SELECT id, razao_social, cnpj, whatsapp, tipo_planta, cidade, uf
            FROM fornecedores
            WHERE razao_social LIKE ? OR cnpj LIKE ? OR cidade LIKE ?
        """, (f"%{busca}%", f"%{busca}%", f"%{busca}%"))
    else:
        cursor.execute("SELECT id, razao_social, cnpj, whatsapp, tipo_planta, cidade, uf FROM fornecedores")
    fornecedores = cursor.fetchall()

    # ---- Consulta Usuários/Cadastros com filtro por nome ou email ----
    if busca:
        cursor.execute("""
            SELECT id, nome, email FROM usuarios
            WHERE nome LIKE ? OR email LIKE ?
        """, (f"%{busca}%", f"%{busca}%"))
    else:
        cursor.execute("SELECT id, nome, email FROM usuarios")
    usuarios = cursor.fetchall()

    # ---- Consulta Plantios com dados do usuário e fornecedor (JOIN) ----
    # Busca todos os plantios independente do filtro de texto, ordenados por data
    cursor.execute("""
        SELECT pg.id, pg.data_plantio, pg.especie, pg.municipio,
               pg.status, pg.justificativa, pg.criado_em,
               u.nome, COALESCE(f.razao_social, 'Não informado') AS fornecedor_nome
        FROM plantas_go pg
        JOIN usuarios u ON u.id = pg.responsavel_id
        LEFT JOIN fornecedores f ON f.id = pg.fornecedor_id
        ORDER BY pg.criado_em DESC
    """)
    plantios = cursor.fetchall()

    conn.close()

    return render_template("admin_painel.html",
                           fornecedores=fornecedores,
                           usuarios=usuarios,
                           plantios=plantios,
                           busca=busca,
                           tipo=tipo)


# ===================== ROTA LOGOUT ADMINISTRADOR =====================
# Encerra a sessão do administrador e redireciona para o login.
@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect("/admin/login")

from db import get_db
import secrets
import random
import os
import re
import uuid
import smtplib
import qrcode
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from flask import render_template, request, redirect, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# ===================== VALIDAÇÃO DE CPF (ALGORITMO RECEITA FEDERAL) =====================
# Implementa os dois dígitos verificadores conforme regra oficial da Receita Federal.
# Retorna True se o CPF é matematicamente válido (estrutura e dígitos corretos).
# Não requer API externa — válido para os casos (a) e (c) da regra de negócio:
#   - CPF inexistente/inventado  → retorna False (bloqueia o cadastro)
#   - CPF com restrições (ativo) → retorna True  (permite o cadastro)
# A Receita Federal não oferece API pública gratuita para consulta de situação cadastral.
def validar_cpf(cpf_raw):
    digits = re.sub(r"\D", "", str(cpf_raw or ""))

    # Deve ter exatamente 11 dígitos
    if len(digits) != 11:
        return False

    # CPFs com todos os dígitos iguais são inválidos pela Receita (ex: 111.111.111-11)
    if len(set(digits)) == 1:
        return False

    # Calcula o 1º dígito verificador
    soma  = sum(int(digits[i]) * (10 - i) for i in range(9))
    resto = (soma * 10) % 11
    if resto >= 10:
        resto = 0
    if resto != int(digits[9]):
        return False

    # Calcula o 2º dígito verificador
    soma  = sum(int(digits[i]) * (11 - i) for i in range(10))
    resto = (soma * 10) % 11
    if resto >= 10:
        resto = 0
    if resto != int(digits[10]):
        return False

    return True


# Pasta de destino dos uploads de fotos de plantio
UPLOAD_FOLDER  = os.path.join(os.path.dirname(__file__), "static", "uploads")
QRCODE_FOLDER  = os.path.join(os.path.dirname(__file__), "static", "qrcodes")
PIX_FOLDER     = os.path.join(os.path.dirname(__file__), "static", "pix")
EXTENSOES_PERMITIDAS = {"jpg", "jpeg", "png"}

from app import app


# ===================== HELPER: ENVIAR EMAIL =====================
# Envia um email HTML via Gmail SMTP usando as variáveis de ambiente
# EMAIL_REMETENTE e EMAIL_SENHA (senha de app do Gmail, não a senha normal).
# Retorna True se enviado com sucesso, False caso contrário.
def enviar_email(destinatario, assunto, corpo_html):
    remetente   = os.environ.get("EMAIL_REMETENTE", "projetoplantandovida@gmail.com")
    senha_email = os.environ.get("EMAIL_SENHA", "")

    # Sem senha configurada, não tenta enviar (evita erro em desenvolvimento)
    if not senha_email:
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = assunto
        msg["From"]    = remetente
        msg["To"]      = destinatario
        msg.attach(MIMEText(corpo_html, "html"))

        # Conecta ao SMTP do Gmail com TLS na porta 587
        with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
            smtp.starttls()
            smtp.login(remetente, senha_email)
            smtp.sendmail(remetente, destinatario, msg.as_string())
        return True
    except Exception:
        return False


# ===================== HELPER: ENVIAR TOKEN DE CADASTRO =====================
# Gera um código de 6 dígitos, salva na sessão e envia por email.
# Retorna o token gerado para que o chamador possa exibi-lo em flash
# caso o envio de email falhe (dev sem EMAIL_SENHA configurado).
def _enviar_token_cadastro(email_dest, nome):
    # Gera token numérico de 6 dígitos
    token = "".join(random.choices("0123456789", k=6))

    # Armazena token na sessão Flask (não expira até o servidor reiniciar)
    session["cadastro_token"] = token

    corpo_html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:auto;padding:24px;">
      <h2 style="color:#166534;">🌱 Plantando Vida — Verificação de Cadastro</h2>
      <p>Olá, <strong>{nome}</strong>!</p>
      <p>Use o código abaixo para concluir seu cadastro na plataforma:</p>
      <div style="font-size:42px;font-weight:bold;letter-spacing:14px;color:#166534;
                  text-align:center;padding:24px;background:#f0fdf4;border-radius:12px;
                  margin:20px 0;">
        {token}
      </div>
      <p style="font-size:12px;color:#6b7280;">
        Este código é válido para uso imediato e não deve ser compartilhado com ninguém.
      </p>
    </div>
    """
    enviar_email(email_dest, "Código de verificação — Plantando Vida", corpo_html)
    return token


# ===================== HELPER: GERAR QR CODE =====================
# Gera um QR Code PNG com o CNPJ (apenas dígitos) do fornecedor.
# O arquivo é salvo em static/qrcodes/ com nome qr_<CNPJ>.png.
# Retorna o nome do arquivo para ser exibido no template.
# O conteúdo do QR Code é o CNPJ puro (14 dígitos), validado no scan.
def gerar_qrcode(cnpj):
    # Remove pontuação — o QR Code carrega apenas os 14 dígitos
    cnpj_limpo = re.sub(r"\D", "", cnpj)
    os.makedirs(QRCODE_FOLDER, exist_ok=True)
    nome_arquivo = f"qr_{cnpj_limpo}.png"
    caminho      = os.path.join(QRCODE_FOLDER, nome_arquivo)

    # Só regera se o arquivo ainda não existir
    if not os.path.exists(caminho):
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(cnpj_limpo)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#166534", back_color="white")
        img.save(caminho)

    return nome_arquivo


# ===================== HELPER: ENVIAR QR CODE POR EMAIL =====================
# Envia o QR Code do fornecedor como imagem inline no corpo do email.
# Requer EMAIL_REMETENTE e EMAIL_SENHA configurados no ambiente.
# Retorna True se enviado com sucesso, False caso contrário.
def enviar_qrcode_email(destinatario, razao_social, cnpj):
    remetente   = os.environ.get("EMAIL_REMETENTE", "projetoplantandovida@gmail.com")
    senha_email = os.environ.get("EMAIL_SENHA", "")

    if not senha_email:
        return False

    # Garante que o QR Code existe antes de enviar
    nome_arquivo = gerar_qrcode(cnpj)
    caminho      = os.path.join(QRCODE_FOLDER, nome_arquivo)

    try:
        # Estrutura multipart/related para embutir a imagem no HTML
        msg = MIMEMultipart("related")
        msg["Subject"] = f"Seu QR Code de Credenciamento — {razao_social}"
        msg["From"]    = remetente
        msg["To"]      = destinatario

        corpo_html = f"""
        <div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;padding:24px;
                    border:1px solid #d1fae5;border-radius:12px;">
            <h2 style="color:#166534;">🌱 Plantando Vida — Seu QR Code</h2>
            <p>Olá, <strong>{razao_social}</strong>!</p>
            <p>Seu QR Code de credenciamento está disponível abaixo.<br>
               Apresente-o aos clientes para validar os plantios adquiridos em seu estabelecimento.</p>
            <div style="text-align:center;margin:20px 0;">
                <img src="cid:qrcode_img" width="200" height="200"
                     style="border:1px solid #d1fae5;border-radius:8px;padding:8px;" />
            </div>
            <p style="color:#6b7280;font-size:12px;">
                CNPJ: {cnpj}<br>
                O QR Code também está acessível no seu painel de fornecedor.
            </p>
        </div>
        """

        alternativa = MIMEMultipart("alternative")
        alternativa.attach(MIMEText(corpo_html, "html"))
        msg.attach(alternativa)

        # Anexa a imagem com Content-ID referenciado no HTML (cid:qrcode_img)
        with open(caminho, "rb") as f:
            img_mime = MIMEImage(f.read(), _subtype="png")
        img_mime.add_header("Content-ID", "<qrcode_img>")
        img_mime.add_header("Content-Disposition", "inline", filename=nome_arquivo)
        msg.attach(img_mime)

        with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
            smtp.starttls()
            smtp.login(remetente, senha_email)
            smtp.sendmail(remetente, destinatario, msg.as_string())
        return True
    except Exception:
        return False


# ===================== API: BUSCAR CNPJ POR CIDADE =====================


# ===================== ROTA HOME =====================
# Página inicial do sistema com apresentação e botões de acesso.
@app.route('/')
def home():
    return render_template('index.html')


# ===================== ROTA DE CADASTRO =====================
# Fluxo de 3 etapas para garantir unicidade de CPF e verificação por email:
#
# Etapa 1 "dados"  → recebe nome/email/CPF/telefone/data_nasc,
#                    verifica unicidade no banco e envia token de 6 dígitos.
# Etapa 2 "token"  → usuário informa o token recebido por email.
# Etapa 3 "senha"  → usuário define e confirma a senha; conta é criada.
#
# Os dados temporários ficam na sessão até a criação ser concluída.
@app.route("/cadastros", methods=["GET", "POST"])
def cadastro():
    # ── GET: exibe o formulário na etapa correta ──
    if request.method == "GET":
        etapa   = request.args.get("etapa", "dados")
        pending = session.get("pending_cadastro", {})

        # Em desenvolvimento (FLASK_ENV != "production"), expõe o token na
        # página de verificação para facilitar testes locais.
        # Em produção (Render com FLASK_ENV=production) o bloco não é exibido.
        dev_token = None
        if etapa == "token" and os.environ.get("FLASK_ENV") != "production":
            dev_token = session.get("cadastro_token")

        return render_template("cadastros.html", etapa=etapa, pending=pending, dev_token=dev_token)

    # ── POST: processa a etapa informada pelo campo oculto "etapa" ──
    etapa = request.form.get("etapa", "dados")

    # ── Etapa 1: valida unicidade e envia token ──
    if etapa == "dados":
        nome                = request.form["nome"].strip()
        email               = request.form["email"].strip().lower()
        cpf                 = request.form["cpf"].strip()
        telefone            = request.form["telefone"].strip()
        data_nascimento     = request.form["data_nascimento"].strip()
        # Campos de localização adicionados no cadastro
        uf     = request.form.get("uf", "").strip()
        cidade = request.form.get("cidade", "").strip()

        # Normaliza CPF (somente dígitos) para comparação neutra de formatação
        cpf_numeros = re.sub(r"\D", "", cpf)

        # Valida CPF pelo algoritmo oficial da Receita Federal (dígitos verificadores).
        # CPFs inventados ou com formato inválido são rejeitados antes de qualquer consulta ao banco.
        if not validar_cpf(cpf_numeros):
            flash("Informe um CPF Válido.", "erro")
            return redirect("/cadastros")

        conn   = get_db()
        cursor = conn.cursor()

        # Verifica se CPF já está cadastrado (independente de máscara)
        cursor.execute(
            "SELECT id FROM usuarios WHERE REPLACE(REPLACE(cpf,'.',''),'-','') = ?",
            (cpf_numeros,)
        )
        if cursor.fetchone():
            conn.close()
            flash("CPF já cadastrado. Entre em contato se isso for um engano.", "erro")
            return redirect("/cadastros")

        # Verifica se email já está cadastrado
        cursor.execute("SELECT id FROM usuarios WHERE email = ?", (email,))
        if cursor.fetchone():
            conn.close()
            flash("Este email já está cadastrado. Tente outro ou faça login.", "erro")
            return redirect("/cadastros")
        conn.close()

        # Salva dados temporários na sessão e gera + envia token
        session["pending_cadastro"] = {
            "nome": nome, "email": email, "cpf": cpf,
            "telefone": telefone, "data_nascimento": data_nascimento,
            "uf": uf, "cidade": cidade,
        }
        session["cadastro_verificado"] = False

        token = _enviar_token_cadastro(email, nome)

        # Em produção envia email; localmente apenas informa que o código foi gerado
        if os.environ.get("FLASK_ENV") == "production":
            flash(f"Código enviado para {email}. Verifique sua caixa de entrada.", "sucesso")
        else:
            flash("Código gerado. Veja o código exibido abaixo.", "sucesso")

        return redirect("/cadastros?etapa=token")

    # ── Etapa 2: valida o token informado pelo usuário ──
    elif etapa == "token":
        token_informado = request.form.get("token", "").strip()
        token_esperado  = session.get("cadastro_token", "")

        if not token_esperado or token_informado != token_esperado:
            flash("Código inválido. Verifique o email e tente novamente.", "erro")
            return redirect("/cadastros?etapa=token")

        # Marca sessão como verificada e avança para criação de senha
        session["cadastro_verificado"] = True
        return redirect("/cadastros?etapa=senha")

    # ── Etapa 3: cria a conta com a senha definida pelo usuário ──
    elif etapa == "senha":
        # Garante que o usuário passou pela verificação de token
        if not session.get("cadastro_verificado"):
            flash("Sessão expirada. Inicie o cadastro novamente.", "erro")
            return redirect("/cadastros")

        senha    = request.form.get("senha", "")
        confirma = request.form.get("confirma_senha", "")

        # Valida correspondência das senhas antes de gravar
        if senha != confirma:
            flash("As senhas não coincidem. Tente novamente.", "erro")
            return redirect("/cadastros?etapa=senha")

        pending = session.get("pending_cadastro")
        if not pending:
            flash("Sessão expirada. Inicie o cadastro novamente.", "erro")
            return redirect("/cadastros")

        # Grava o novo usuário com senha hasheada
        conn   = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """INSERT INTO usuarios
                   (nome, email, senha, cpf, telefone, data_nascimento, uf, cidade)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pending["nome"], pending["email"],
                    generate_password_hash(senha),
                    pending["cpf"], pending["telefone"], pending["data_nascimento"],
                    pending.get("uf"), pending.get("cidade"),
                )
            )
            conn.commit()
        except Exception:
            flash("Erro ao criar conta. O email pode já estar cadastrado.", "erro")
            return redirect("/cadastros")
        finally:
            conn.close()

        # Limpa os dados temporários da sessão após criação bem-sucedida
        session.pop("pending_cadastro", None)
        session.pop("cadastro_token", None)
        session.pop("cadastro_verificado", None)

        flash("Conta criada com sucesso! Faça login para continuar.", "sucesso")
        return redirect("/login")

    # Etapa desconhecida — volta ao início
    return redirect("/cadastros")


# ===================== ROTA DE LOGIN =====================
# GET:  exibe o formulário de login.
# POST: valida email, CPF e senha no banco.
#       CPF é normalizado (somente dígitos) antes da comparação para aceitar
#       qualquer formatação digitada pelo usuário.
#       Mensagem de erro genérica por segurança — não expõe qual campo falhou.
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        cpf   = re.sub(r"\D", "", request.form.get("cpf", ""))  # Somente dígitos
        senha = request.form["senha"]

        conn   = get_db()
        cursor = conn.cursor()

        # Busca o usuário pelo email e valida CPF e senha na mesma etapa
        cursor.execute("SELECT * FROM usuarios WHERE email = ?", (email,))
        usuario = cursor.fetchone()
        conn.close()

        # usuarios: id[0] nome[1] email[2] senha[3] cpf[4] telefone[5] data_nascimento[6]
        # usuario[4] = cpf armazenado; normaliza para comparação neutra de formatação
        cpf_banco = re.sub(r"\D", "", usuario[4]) if usuario and usuario[4] else ""

        # Valida: usuário existe + CPF confere + senha confere com o hash
        if usuario and cpf_banco == cpf and check_password_hash(usuario[3], senha):
            session["usuario_id"]   = usuario[0]
            session["usuario_nome"] = usuario[1]
            return redirect("/dashboard")

        # Credenciais inválidas: mensagem genérica para não expor qual campo falhou
        flash("Email, CPF ou senha incorretos. Tente novamente.", "erro")
        return redirect("/login")

    return render_template("login.html")


# ===================== ROTA DO DASHBOARD =====================
# Exibe o painel do usuário após login.
# Se o usuário não estiver logado (sem sessão), redireciona para /login.
@app.route("/dashboard")
def dashboard():
    if "usuario_id" not in session:
        return redirect("/login")

    conn   = get_db()
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

        conn   = get_db()
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

    # GET: busca fornecedores ativos e dados bancários para o modal de compra
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, razao_social, cnpj, cidade, uf, tipo_planta, whatsapp, maps_link FROM fornecedores WHERE ativo = 1 ORDER BY uf, cidade")
    fornecedores = cursor.fetchall()

    # Dados bancários exibidos no modal de compra para o usuário efetuar o pagamento
    cursor.execute("SELECT nome_empresarial, banco, conta, agencia, chave_pix, qrcode_pix FROM dados_bancarios LIMIT 1")
    dados_bancarios = cursor.fetchone()
    conn.close()

    return render_template("plantio_credenciado.html", fornecedores=fornecedores, dados_bancarios=dados_bancarios)


# ===================== ROTA MEUS PLANTIOS (PENDENTES / TODOS) =====================
# Exibe todos os plantios do usuário logado com seus respectivos status.
# Acesso restrito a usuários autenticados.
@app.route("/plantios/pendentes")
def plantios_pendentes():
    if "usuario_id" not in session:
        return redirect("/login")

    conn   = get_db()
    cursor = conn.cursor()

    # Busca todos os plantios do usuário (todos os status).
    # Plantios aprovados exibem o formulário de acompanhamentos inline.
    # p[0]=id  p[1]=data_plantio  p[2]=especie  p[3]=municipio  p[4]=bairro
    # p[5]=status  p[6]=justificativa  p[7]=criado_em  p[8]=fornecedor_nome
    # p[9]=acompanhamento_1  p[10]=foto_1  p[11]=acompanhamento_2  p[12]=foto_2
    # p[13]=acompanhamento_3  p[14]=foto_3
    cursor.execute("""
        SELECT pg.id, pg.data_plantio, pg.especie, pg.municipio, pg.bairro,
               pg.status, pg.justificativa, pg.criado_em,
               COALESCE(f.razao_social, 'Fornecedor não informado') AS fornecedor_nome,
               pg.acompanhamento_1, pg.foto_1,
               pg.acompanhamento_2, pg.foto_2,
               pg.acompanhamento_3, pg.foto_3
        FROM plantas_go pg
        LEFT JOIN fornecedores f ON f.id = pg.fornecedor_id
        WHERE pg.responsavel_id = ?
        ORDER BY pg.criado_em DESC
    """, (session["usuario_id"],))
    plantios = cursor.fetchall()

    # Busca compras do usuário para exibir status de pagamento na mesma tela
    # c[0]=id  c[1]=especie_nome  c[2]=tipo_planta  c[3]=valor
    # c[4]=status  c[5]=criado_em  c[6]=fornecedor_nome  c[7]=comprovante  c[8]=plantio_id
    cursor.execute("""
        SELECT c.id, c.especie_nome, c.tipo_planta, c.valor,
               c.status, c.criado_em,
               COALESCE(f.razao_social, 'Fornecedor não informado') AS fornecedor_nome,
               c.comprovante, c.plantio_id
        FROM compras c
        LEFT JOIN fornecedores f ON f.id = c.fornecedor_id
        WHERE c.usuario_id = ?
        ORDER BY c.criado_em DESC
    """, (session["usuario_id"],))
    compras = cursor.fetchall()
    conn.close()

    return render_template("plantios_pendentes.html", plantios=plantios, compras=compras)


# ===================== ROTA ACOMPANHAMENTOS DO PLANTIO =====================
# Salva as datas e fotos dos 3 acompanhamentos de um plantio aprovado.
# Condição: plantio deve pertencer ao usuário logado E ter status 'aprovado'.
# Acesso restrito a usuários autenticados (session["usuario_id"]).
@app.route("/plantio/<int:pid>/acompanhamentos", methods=["POST"])
def salvar_acompanhamentos(pid):
    if "usuario_id" not in session:
        return redirect("/login")

    conn   = get_db()
    cursor = conn.cursor()

    # Verifica posse e status: somente plantios 'aprovado' do próprio usuário
    cursor.execute(
        "SELECT id FROM plantas_go WHERE id = ? AND responsavel_id = ? AND status = 'aprovado'",
        (pid, session["usuario_id"])
    )
    if not cursor.fetchone():
        conn.close()
        flash("Acompanhamento disponível apenas para plantios aprovados.", "erro")
        return redirect("/plantios/pendentes")

    # Lê datas dos acompanhamentos (campos opcionais)
    acomp_1 = request.form.get("acompanhamento_1") or None
    acomp_2 = request.form.get("acompanhamento_2") or None
    acomp_3 = request.form.get("acompanhamento_3") or None

    # Processa uploads de foto — salva em static/uploads/ e mantém nome atual se não enviado
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    def _salvar_foto(campo_nome, campo_atual):
        """Salva nova foto se enviada; caso contrário mantém o arquivo já gravado."""
        arquivo = request.files.get(campo_nome)
        if arquivo and arquivo.filename:
            ext = arquivo.filename.rsplit(".", 1)[-1].lower()
            if ext in EXTENSOES_PERMITIDAS:
                nome = f"acomp_{pid}_{campo_nome}_{uuid.uuid4().hex[:8]}.{ext}"
                arquivo.save(os.path.join(UPLOAD_FOLDER, nome))
                return nome
        return campo_atual  # mantém foto anterior

    # Carrega fotos atuais para não apagar ao omitir o upload
    cursor.execute(
        "SELECT foto_1, foto_2, foto_3 FROM plantas_go WHERE id = ?", (pid,)
    )
    row      = cursor.fetchone()
    foto_1   = _salvar_foto("foto_1", row[0] if row else None)
    foto_2   = _salvar_foto("foto_2", row[1] if row else None)
    foto_3   = _salvar_foto("foto_3", row[2] if row else None)

    # Atualiza os 3 acompanhamentos no registro de plantio
    cursor.execute("""
        UPDATE plantas_go
        SET acompanhamento_1 = ?, foto_1 = ?,
            acompanhamento_2 = ?, foto_2 = ?,
            acompanhamento_3 = ?, foto_3 = ?
        WHERE id = ?
    """, (acomp_1, foto_1, acomp_2, foto_2, acomp_3, foto_3, pid))
    conn.commit()
    conn.close()

    flash("Acompanhamentos salvos com sucesso.", "sucesso")
    return redirect("/plantios/pendentes")


# ===================== ROTA PLANTIOS APROVADOS =====================
# Exibe somente os plantios com status 'aprovado' do usuário logado.
# Acesso restrito a usuários autenticados (session["usuario_id"]).
@app.route("/plantios/aprovados")
def plantios_aprovados():
    if "usuario_id" not in session:
        return redirect("/login")

    conn   = get_db()
    cursor = conn.cursor()

    # Busca apenas os plantios aprovados pelo administrador.
    # Inclui foto_1 para exibição de thumbnail no card.
    cursor.execute("""
        SELECT pg.id, pg.data_plantio, pg.especie, pg.municipio, pg.bairro,
               pg.criado_em,
               COALESCE(f.razao_social, 'Fornecedor não informado') AS fornecedor_nome,
               pg.foto_1
        FROM plantas_go pg
        LEFT JOIN fornecedores f ON f.id = pg.fornecedor_id
        WHERE pg.responsavel_id = ? AND pg.status = 'aprovado'
        ORDER BY pg.criado_em DESC
    """, (session["usuario_id"],))
    plantios = cursor.fetchall()
    conn.close()

    return render_template("plantios_aprovados.html", plantios=plantios)


# ===================== ROTA ADMIN: SALVAR DADOS DO USUÁRIO =====================
# Atualiza todos os campos editáveis do usuário (nome, email, CPF, telefone,
# data de nascimento). Acesso restrito ao administrador.
@app.route("/admin/usuario/<int:usuario_id>/salvar", methods=["POST"])
def admin_usuario_salvar(usuario_id):
    if not session.get("admin"):
        return redirect("/admin/login")

    nome            = request.form.get("nome", "").strip()
    email           = request.form.get("email", "").strip().lower()
    cpf             = request.form.get("cpf", "").strip()
    telefone        = request.form.get("telefone", "").strip()
    data_nascimento = request.form.get("data_nascimento", "").strip()

    conn   = get_db()
    cursor = conn.cursor()

    # Verifica se o novo email já pertence a outro usuário
    cursor.execute("SELECT id FROM usuarios WHERE email = ? AND id != ?", (email, usuario_id))
    if cursor.fetchone():
        conn.close()
        flash("Este email já está em uso por outro cadastro.", "erro")
        return redirect("/admin/painel?tipo=usuarios")

    # Atualiza os dados do usuário pelo id
    cursor.execute("""
        UPDATE usuarios
        SET nome=?, email=?, cpf=?, telefone=?, data_nascimento=?
        WHERE id=?
    """, (nome, email, cpf, telefone, data_nascimento, usuario_id))
    conn.commit()
    conn.close()

    flash(f"Dados do usuário #{usuario_id} atualizados com sucesso.", "sucesso")
    return redirect("/admin/painel?tipo=usuarios")


# ===================== ROTA ADMIN: LIMPAR SENHA DO USUÁRIO =====================
# Define a senha como string vazia, impedindo o login do usuário até que ele
# redefina sua senha pelo fluxo "Esqueci minha senha".
@app.route("/admin/usuario/<int:usuario_id>/limpar-senha", methods=["POST"])
def admin_usuario_limpar_senha(usuario_id):
    if not session.get("admin"):
        return redirect("/admin/login")

    conn   = get_db()
    cursor = conn.cursor()

    # Senha vazia impossibilita o login via check_password_hash para qualquer input
    cursor.execute("UPDATE usuarios SET senha=? WHERE id=?", ("", usuario_id))
    conn.commit()
    conn.close()

    flash(f"Senha do usuário #{usuario_id} foi limpa. O usuário precisará redefini-la.", "sucesso")
    return redirect("/admin/painel?tipo=usuarios")


# ===================== ROTA ADMIN: EXCLUIR USUÁRIO =====================
# Remove permanentemente o usuário do banco de dados.
# Acesso restrito ao administrador.
@app.route("/admin/usuario/<int:usuario_id>/excluir", methods=["POST"])
def admin_usuario_excluir(usuario_id):
    if not session.get("admin"):
        return redirect("/admin/login")

    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM usuarios WHERE id=?", (usuario_id,))
    conn.commit()
    conn.close()

    flash(f"Usuário #{usuario_id} excluído com sucesso.", "sucesso")
    return redirect("/admin/painel?tipo=usuarios")


# ===================== ROTA ADMIN: APROVAR PLANTIO =====================
# Altera o status do plantio para 'aprovado' e envia email de notificação ao usuário.
# Acesso restrito ao administrador (session["admin"]).
@app.route("/admin/plantio/<int:plantio_id>/aprovar", methods=["POST"])
def admin_aprovar_plantio(plantio_id):
    if not session.get("admin"):
        return redirect("/admin/login")

    conn   = get_db()
    cursor = conn.cursor()

    # Busca dados do plantio e email/nome do usuário dono do registro
    cursor.execute("""
        SELECT pg.especie, pg.municipio, pg.bairro, pg.data_plantio,
               u.nome, u.email
        FROM plantas_go pg
        JOIN usuarios u ON u.id = pg.responsavel_id
        WHERE pg.id = ?
    """, (plantio_id,))
    dados = cursor.fetchone()

    # Atualiza status para aprovado e limpa qualquer justificativa anterior
    cursor.execute(
        "UPDATE plantas_go SET status = 'aprovado', justificativa = NULL WHERE id = ?",
        (plantio_id,)
    )
    conn.commit()
    conn.close()

    # Envia email de notificação ao usuário se os dados foram encontrados
    if dados:
        especie, municipio, bairro, data_plantio, nome_usuario, email_usuario = dados
        corpo_html = f"""
        <div style="font-family:sans-serif;max-width:520px;margin:auto;background:#f0fdf4;
                    border-radius:12px;padding:32px;border:1px solid #bbf7d0">
            <div style="text-align:center;margin-bottom:24px">
                <span style="font-size:48px">🌳</span>
                <h1 style="color:#15803d;font-size:20px;margin:12px 0 4px">
                    Plantio Aprovado!
                </h1>
                <p style="color:#4b7a58;font-size:14px;margin:0">
                    Parabéns, {nome_usuario}! Seu registro foi confirmado pela equipe.
                </p>
            </div>

            <div style="background:#ffffff;border-radius:8px;padding:16px;
                        border-left:4px solid #16a34a;margin-bottom:20px">
                <p style="margin:0 0 6px;font-size:13px;color:#6b7280">Detalhes do plantio aprovado:</p>
                <p style="margin:4px 0;font-size:14px;color:#111827">
                    🌿 <strong>Espécie:</strong> {especie}
                </p>
                <p style="margin:4px 0;font-size:14px;color:#111827">
                    📍 <strong>Local:</strong> {bairro}, {municipio}
                </p>
                <p style="margin:4px 0;font-size:14px;color:#111827">
                    📅 <strong>Data do plantio:</strong> {data_plantio}
                </p>
            </div>

            <p style="font-size:13px;color:#4b5563;text-align:center">
                Acesse seu painel para ver todos os plantios aprovados.
            </p>

            <div style="text-align:center;margin-top:20px">
                <a href="{os.environ.get('APP_URL', 'http://localhost:5000')}/plantios/aprovados"
                   style="background:#16a34a;color:#ffffff;padding:10px 24px;border-radius:8px;
                          text-decoration:none;font-weight:bold;font-size:14px">
                    Ver meus plantios aprovados
                </a>
            </div>

            <p style="font-size:11px;color:#9ca3af;text-align:center;margin-top:24px">
                Plantando Vida — juntos por um mundo mais verde 🌱
            </p>
        </div>
        """
        enviar_email(email_usuario, "🌳 Plantio Aprovado — Plantando Vida", corpo_html)

    flash("Plantio aprovado com sucesso.", "sucesso")
    return redirect("/admin/painel?tipo=plantios")


# ===================== ROTA ADMIN: REPROVAR PLANTIO =====================
# Altera o status do plantio para 'reprovado' com justificativa obrigatória (máx. 100 chars).
# Envia email de notificação ao usuário com a justificativa.
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

    conn   = get_db()
    cursor = conn.cursor()

    # Busca dados do plantio e email/nome do usuário dono do registro
    cursor.execute("""
        SELECT pg.especie, pg.municipio, pg.bairro, pg.data_plantio,
               u.nome, u.email
        FROM plantas_go pg
        JOIN usuarios u ON u.id = pg.responsavel_id
        WHERE pg.id = ?
    """, (plantio_id,))
    dados = cursor.fetchone()

    # Atualiza status para reprovado e grava a justificativa
    cursor.execute(
        "UPDATE plantas_go SET status = 'reprovado', justificativa = ? WHERE id = ?",
        (justificativa, plantio_id)
    )
    conn.commit()
    conn.close()

    # Envia email de notificação ao usuário com a justificativa da reprovação
    if dados:
        especie, municipio, bairro, data_plantio, nome_usuario, email_usuario = dados
        corpo_html = f"""
        <div style="font-family:sans-serif;max-width:520px;margin:auto;background:#fff7f7;
                    border-radius:12px;padding:32px;border:1px solid #fecaca">
            <div style="text-align:center;margin-bottom:24px">
                <span style="font-size:48px">🌱</span>
                <h1 style="color:#b91c1c;font-size:20px;margin:12px 0 4px">
                    Registro não aprovado
                </h1>
                <p style="color:#7f5252;font-size:14px;margin:0">
                    Olá, {nome_usuario}. Seu plantio foi analisado e não pôde ser aprovado neste momento.
                </p>
            </div>

            <div style="background:#ffffff;border-radius:8px;padding:16px;
                        border-left:4px solid #ef4444;margin-bottom:20px">
                <p style="margin:0 0 6px;font-size:13px;color:#6b7280">Detalhes do plantio:</p>
                <p style="margin:4px 0;font-size:14px;color:#111827">
                    🌿 <strong>Espécie:</strong> {especie}
                </p>
                <p style="margin:4px 0;font-size:14px;color:#111827">
                    📍 <strong>Local:</strong> {bairro}, {municipio}
                </p>
                <p style="margin:4px 0;font-size:14px;color:#111827">
                    📅 <strong>Data do plantio:</strong> {data_plantio}
                </p>
            </div>

            <div style="background:#fef2f2;border-radius:8px;padding:14px;margin-bottom:20px">
                <p style="margin:0 0 6px;font-size:13px;color:#6b7280;font-weight:bold">
                    Motivo informado pela equipe:
                </p>
                <p style="margin:0;font-size:14px;color:#7f1d1d;font-style:italic">
                    "{justificativa}"
                </p>
            </div>

            <p style="font-size:13px;color:#4b5563;text-align:center">
                Em caso de dúvidas, entre em contato com a equipe Plantando Vida.
                Você pode registrar um novo plantio a qualquer momento.
            </p>

            <div style="text-align:center;margin-top:20px">
                <a href="{os.environ.get('APP_URL', 'http://localhost:5000')}/plantios/pendentes"
                   style="background:#6b7280;color:#ffffff;padding:10px 24px;border-radius:8px;
                          text-decoration:none;font-weight:bold;font-size:14px">
                    Ver meus registros
                </a>
            </div>

            <p style="font-size:11px;color:#9ca3af;text-align:center;margin-top:24px">
                Plantando Vida — juntos por um mundo mais verde 🌱
            </p>
        </div>
        """
        enviar_email(email_usuario, "Atualização sobre seu plantio — Plantando Vida", corpo_html)

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

        conn   = get_db()
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

            # Monta a URL de redefinição usando a variável APP_URL (Railway) ou localhost
            base_url = os.environ.get("APP_URL", "http://127.0.0.1:5000")
            link     = f"{base_url}/redefinir-senha/{token}"

            # Corpo HTML do email com o link de redefinição
            corpo_html = f"""
            <div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;padding:24px;border:1px solid #d1fae5;border-radius:12px;">
                <h2 style="color:#166534;">🌱 Plantando Vida — Redefinição de Senha</h2>
                <p>Recebemos uma solicitação para redefinir a senha da sua conta.</p>
                <p>Clique no botão abaixo para criar uma nova senha:</p>
                <a href="{link}" style="display:inline-block;margin:16px 0;padding:12px 24px;background:#16a34a;color:#fff;border-radius:8px;text-decoration:none;font-weight:bold;">
                    Redefinir minha senha
                </a>
                <p style="color:#6b7280;font-size:12px;">Se você não solicitou isso, ignore este email. O link expira após o uso.</p>
            </div>
            """

            # Tenta enviar o email; se EMAIL_SENHA não estiver configurada, exibe o link na tela
            enviou = enviar_email(email, "Redefinição de Senha — Plantando Vida", corpo_html)
            return render_template("esqueci_senha.html", enviado=True,
                                   link=None if enviou else link)

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
    conn   = get_db()
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
        email        = request.form.get("email", "").strip()
        cidade       = request.form.get("cidade", "")
        uf           = request.form.get("uf", "").upper()
        maps_link    = request.form.get("maps_link", "")
        tipo_planta  = request.form.get("tipo_planta", "")

        # Aplica hash na senha antes de salvar no banco
        senha = generate_password_hash(request.form["senha"])

        conn   = get_db()
        cursor = conn.cursor()

        try:
            cursor.execute(
                "INSERT INTO fornecedores (razao_social, cnpj, whatsapp, tipo_planta, maps_link, senha, cidade, uf, email) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (razao_social, cnpj, whatsapp, tipo_planta, maps_link, senha, cidade, uf, email)
            )
            conn.commit()

            # Gera o QR Code imediatamente após o cadastro
            gerar_qrcode(cnpj)

            # Tenta enviar o QR Code por email se o endereço foi informado
            if email:
                enviou = enviar_qrcode_email(email, razao_social, cnpj)
                if enviou:
                    flash("Cadastro realizado! QR Code enviado para seu email.", "sucesso")
                else:
                    flash("Cadastro realizado! QR Code gerado — configure o email para receber por email.", "sucesso")
            else:
                flash("Cadastro realizado com sucesso! Bem-vindo ao seu painel.", "sucesso")

            # Busca o id pelo CNPJ para iniciar a sessão
            cursor.execute("SELECT id FROM fornecedores WHERE cnpj = ?", (cnpj,))
            session["fornecedor_id"] = cursor.fetchone()[0]
            conn.close()
            return redirect("/fornecedor/painel")

        except Exception:
            # CNPJ já cadastrado no banco
            flash("Este CNPJ já está cadastrado.", "erro")
            conn.close()
            return redirect("/fornecedor")

    return render_template("fornecedor.html")


# ===================== ROTA LISTA DE FORNECEDORES =====================
# Exibe todos os fornecedores cadastrados no banco com opções de editar e excluir.
@app.route("/fornecedores")
def listar_fornecedores():
    conn   = get_db()
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
    conn   = get_db()
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
    conn   = get_db()
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

        conn   = get_db()
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
    conn   = get_db()
    cursor = conn.cursor()

    if request.method == "POST":
        razao_social = request.form["razao_social"]
        cnpj         = request.form["cnpj"]
        whatsapp     = request.form["whatsapp"]
        email        = request.form.get("email", "").strip()
        cidade       = request.form.get("cidade", "")
        uf           = request.form.get("uf", "").upper()
        tipo_planta  = request.form.get("tipo_planta", "")
        maps_link    = request.form.get("maps_link", "")

        # Atualiza usando o id da sessão — impede alteração de outros cadastros
        cursor.execute("""
            UPDATE fornecedores
            SET razao_social=?, cnpj=?, whatsapp=?, tipo_planta=?, maps_link=?, cidade=?, uf=?, email=?
            WHERE id=?
        """, (razao_social, cnpj, whatsapp, tipo_planta, maps_link, cidade, uf, email, fornecedor_id))
        conn.commit()

        # Regenera o QR Code caso o CNPJ tenha sido alterado
        gerar_qrcode(cnpj)

        session["fornecedor_nome"] = razao_social
        conn.close()

        flash("Cadastro atualizado com sucesso!", "sucesso")
        return redirect("/fornecedor/painel")

    # Busca os dados atuais do fornecedor pelo id da sessão
    cursor.execute("SELECT * FROM fornecedores WHERE id = ?", (fornecedor_id,))
    fornecedor = cursor.fetchone()

    # Busca todas as compras já retiradas por este fornecedor para o grid de estatísticas.
    # r[0]=id  r[1]=especie_nome  r[2]=tipo_planta  r[3]=valor  r[4]=data_validacao
    # r[5]=faturamento_id (NULL = pendente de faturamento; preenchido = já faturado)
    cursor.execute("""
        SELECT id, especie_nome, tipo_planta, valor, data_validacao, faturamento_id
        FROM compras
        WHERE fornecedor_id = ? AND status = 'retirado'
        ORDER BY data_validacao DESC
    """, (fornecedor_id,))
    retiradas = cursor.fetchall()

    # Busca tabela de percentuais de vigência para calcular o saldo líquido do fornecedor.
    # p[0]=id  p[1]=inicio_vigencia  p[2]=perc_fornecedor  p[3]=perc_entidade  p[4]=perc_admin
    cursor.execute("""
        SELECT id, inicio_vigencia, perc_fornecedor, perc_entidade, perc_admin
        FROM percentuais_vigencia
        ORDER BY inicio_vigencia ASC
    """)
    percentuais = cursor.fetchall()

    # Busca histórico de faturamentos deste fornecedor, ordenado do mais recente.
    # fat[0]=id  fat[1]=mes_ref  fat[2]=numero_baixa  fat[3]=quantidade
    # fat[4]=valor_bruto  fat[5]=perc_fornecedor  fat[6]=valor_liquido  fat[7]=data_faturamento
    cursor.execute("""
        SELECT id, mes_ref, numero_baixa, quantidade,
               valor_bruto, perc_fornecedor, valor_liquido, data_faturamento
        FROM faturamentos
        WHERE fornecedor_id = ?
        ORDER BY mes_ref DESC
    """, (fornecedor_id,))
    faturamentos = cursor.fetchall()

    conn.close()

    # Garante que o QR Code existe (gera se ainda não tiver)
    cnpj_atual = fornecedor[2]  # índice 2 = cnpj
    qr_arquivo = gerar_qrcode(cnpj_atual)

    return render_template("fornecedor_painel.html",
                           fornecedor=fornecedor,
                           qr_arquivo=qr_arquivo,
                           retiradas=retiradas,
                           percentuais=percentuais,
                           faturamentos=faturamentos)


# ===================== ROTA VALIDAR VOUCHER DE RETIRADA =====================
# Chamada pelo fornecedor ao escanear o QR Code do voucher do comprador.
# Segurança:
#   - Verifica que a compra pertence a ESTE fornecedor (session["fornecedor_id"]).
#   - Verifica que o status é 'aprovado' (não 'em_analise', 'reprovado' ou já 'retirado').
# Ao validar: muda status para 'retirado' e grava data/hora da validação.
@app.route("/fornecedor/validar-voucher", methods=["POST"])
def fornecedor_validar_voucher():
    if "fornecedor_id" not in session:
        return redirect("/fornecedor/login")

    compra_id_str = request.form.get("compra_id", "").strip()

    # Aceita número puro ou formato #000001
    compra_id_str = compra_id_str.lstrip("#").strip()
    if not compra_id_str.isdigit():
        flash("Número do pedido inválido. Informe apenas os dígitos.", "erro")
        return redirect("/fornecedor/painel")

    compra_id     = int(compra_id_str)
    fornecedor_id = session["fornecedor_id"]

    conn   = get_db()
    cursor = conn.cursor()

    # Valida: compra existe + pertence a este fornecedor + está aprovada
    cursor.execute("""
        SELECT id, especie_nome, tipo_planta, valor
        FROM compras
        WHERE id = ? AND fornecedor_id = ? AND status = 'aprovado'
    """, (compra_id, fornecedor_id))
    compra = cursor.fetchone()

    if not compra:
        conn.close()
        flash("Voucher inválido, já utilizado ou não pertence a este estabelecimento.", "erro")
        return redirect("/fornecedor/painel")

    # Registra a validação com data/hora atual e muda status para 'retirado'
    from datetime import datetime
    data_agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        "UPDATE compras SET status='retirado', data_validacao=? WHERE id=?",
        (data_agora, compra_id)
    )
    conn.commit()
    conn.close()

    flash(
        f"✅ Pedido #{compra_id:06d} validado com sucesso! "
        f"{compra[1]} — R$ {compra[3]:.2f}",
        "sucesso"
    )
    return redirect("/fornecedor/painel")


# ===================== ROTA ENVIAR QR CODE POR EMAIL =====================
# Envia o QR Code do fornecedor logado para o email cadastrado.
# Acesso restrito ao fornecedor autenticado (session["fornecedor_id"]).
@app.route("/fornecedor/painel/qrcode/email", methods=["POST"])
def fornecedor_enviar_qrcode():
    if "fornecedor_id" not in session:
        return redirect("/fornecedor/login")

    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT razao_social, cnpj, email FROM fornecedores WHERE id = ?",
                   (session["fornecedor_id"],))
    f = cursor.fetchone()
    conn.close()

    if not f or not f[2]:
        flash("Nenhum email cadastrado. Atualize seu cadastro com um email válido.", "erro")
        return redirect("/fornecedor/painel")

    razao_social, cnpj, email = f
    enviou = enviar_qrcode_email(email, razao_social, cnpj)

    if enviou:
        flash(f"QR Code enviado com sucesso para {email}!", "sucesso")
    else:
        flash("Não foi possível enviar o email. Verifique as configurações de email do sistema.", "erro")

    return redirect("/fornecedor/painel")


# ===================== ROTA EXCLUIR PRÓPRIO CADASTRO =====================
# Remove o cadastro do fornecedor usando o id da sessão (nunca da URL).
# Após excluir, limpa a sessão e redireciona para a página de cadastro.
@app.route("/fornecedor/painel/excluir", methods=["POST"])
def fornecedor_excluir_proprio():
    if "fornecedor_id" not in session:
        return redirect("/fornecedor/login")

    fornecedor_id = session["fornecedor_id"]
    conn   = get_db()
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

            conn   = get_db()
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

            conn   = get_db()
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
# Lidas das variáveis de ambiente (.env local ou Railway em produção).
# Nunca defina valores reais diretamente no código.
ADMIN_LOGIN = os.environ.get("ADMIN_LOGIN", "")

# A senha do ambiente é hasheada uma única vez na inicialização do app,
# evitando comparação em texto puro durante o login.
_admin_senha_plain = os.environ.get("ADMIN_SENHA", "")
ADMIN_SENHA_HASH = generate_password_hash(_admin_senha_plain) if _admin_senha_plain else ""
del _admin_senha_plain  # Remove a variável com texto puro da memória após o hash


# ===================== ROTA LOGIN ADMINISTRADOR =====================
# GET:  exibe o formulário de login do administrador.
# POST: valida login (comparação direta) e senha (check_password_hash).
#       Se corretos: cria session["admin"] e redireciona para o painel.
#       Se incorretos: exibe mensagem de erro via flash.
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        login = request.form["login"]
        senha = request.form["senha"]

        # Valida login por igualdade e senha via hash — nunca texto puro
        if login == ADMIN_LOGIN and ADMIN_SENHA_HASH and check_password_hash(ADMIN_SENHA_HASH, senha):
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

    conn   = get_db()
    cursor = conn.cursor()

    # Parâmetros de filtro vindos da URL (query string)
    busca = request.args.get("busca", "").strip()
    tipo  = request.args.get("tipo", "fornecedores")  # aba ativa padrão

    # ---- Consulta Fornecedores com filtro por razão social, CNPJ ou cidade ----
    # Inclui ativo e maps_link para permitir edição e exibir status no painel
    if busca:
        cursor.execute("""
            SELECT id, razao_social, cnpj, whatsapp, tipo_planta, cidade, uf, ativo, maps_link, email
            FROM fornecedores
            WHERE razao_social LIKE ? OR cnpj LIKE ? OR cidade LIKE ?
            ORDER BY ativo DESC, razao_social
        """, (f"%{busca}%", f"%{busca}%", f"%{busca}%"))
    else:
        cursor.execute("""
            SELECT id, razao_social, cnpj, whatsapp, tipo_planta, cidade, uf, ativo, maps_link, email
            FROM fornecedores
            ORDER BY ativo DESC, razao_social
        """)
    fornecedores = cursor.fetchall()

    # ---- Consulta Usuários: todos os campos para permitir edição pelo admin ----
    # u[0]=id  u[1]=nome  u[2]=email  u[3]=cpf  u[4]=telefone  u[5]=data_nascimento
    if busca:
        cursor.execute("""
            SELECT id, nome, email, cpf, telefone, data_nascimento FROM usuarios
            WHERE nome LIKE ? OR email LIKE ? OR cpf LIKE ?
        """, (f"%{busca}%", f"%{busca}%", f"%{busca}%"))
    else:
        cursor.execute("SELECT id, nome, email, cpf, telefone, data_nascimento FROM usuarios ORDER BY nome")
    usuarios = cursor.fetchall()

    # ---- Consulta Plantios com dados do usuário, fornecedor, fotos e localização ----
    # p[0]=id  p[1]=data_plantio  p[2]=especie  p[3]=municipio  p[4]=status
    # p[5]=justificativa  p[6]=criado_em  p[7]=nome_usuario  p[8]=fornecedor_nome
    # p[9]=bairro  p[10]=latitude  p[11]=longitude  p[12]=foto_plantio  p[13]=foto_1
    cursor.execute("""
        SELECT pg.id, pg.data_plantio, pg.especie, pg.municipio,
               pg.status, pg.justificativa, pg.criado_em,
               u.nome, COALESCE(f.razao_social, 'Não informado') AS fornecedor_nome,
               pg.bairro, pg.latitude, pg.longitude,
               pg.foto_plantio, pg.foto_1
        FROM plantas_go pg
        JOIN usuarios u ON u.id = pg.responsavel_id
        LEFT JOIN fornecedores f ON f.id = pg.fornecedor_id
        ORDER BY pg.criado_em DESC
    """)
    plantios = cursor.fetchall()

    # ---- Consulta Compras de Mudas (todas, para o admin gerenciar) ----
    # c[0]=id  c[1]=especie_nome  c[2]=tipo_planta  c[3]=valor
    # c[4]=comprovante  c[5]=status  c[6]=criado_em
    # c[7]=usuario_nome  c[8]=usuario_email  c[9]=fornecedor_nome
    cursor.execute("""
        SELECT c.id, c.especie_nome, c.tipo_planta, c.valor,
               c.comprovante, c.status, c.criado_em,
               u.nome, u.email,
               COALESCE(f.razao_social, 'Não informado') AS fornecedor_nome
        FROM compras c
        JOIN usuarios u ON u.id = c.usuario_id
        LEFT JOIN fornecedores f ON f.id = c.fornecedor_id
        ORDER BY c.criado_em DESC
    """)
    compras = cursor.fetchall()

    # ---- Consulta Espécies de Plantas ----
    cursor.execute("SELECT id, nome, tipo, valor FROM especies_plantas ORDER BY nome")
    especies = cursor.fetchall()

    # ---- Consulta Dados Bancários — Admin (registro único) ----
    cursor.execute("SELECT nome_empresarial, banco, conta, agencia, chave_pix, qrcode_pix FROM dados_bancarios LIMIT 1")
    dados_bancarios = cursor.fetchone()

    # ---- Consulta Dados Bancários — Entidade Favorecida (registro único) ----
    cursor.execute("SELECT nome_empresarial, banco, conta, agencia, chave_pix, qrcode_pix FROM dados_bancarios_entidade LIMIT 1")
    dados_bancarios_entidade = cursor.fetchone()

    # ---- Consulta Entidades Favorecidas cadastradas (lista) ----
    # e[0]=id  e[1]=razao_social  e[2]=cnpj  e[3]=whatsapp  e[4]=criado_em
    cursor.execute("SELECT id, razao_social, cnpj, whatsapp, criado_em FROM entidades ORDER BY razao_social")
    entidades = cursor.fetchall()

    # ---- Consulta Percentuais de Vigência — tabela de distribuição do valor das plantas ----
    # p[0]=id  p[1]=inicio_vigencia  p[2]=perc_fornecedor  p[3]=perc_entidade  p[4]=perc_admin
    cursor.execute("""
        SELECT id, inicio_vigencia, perc_fornecedor, perc_entidade, perc_admin, criado_em
        FROM percentuais_vigencia
        ORDER BY inicio_vigencia DESC
    """)
    percentuais = cursor.fetchall()

    # ---- Cálculo dos Fechamentos Mensais Pendentes (Fornecedor, Entidade e Admin) ----
    # Inclui os 3 percentuais para que o helper genérico possa usar o correto por tipo.
    cursor.execute("""
        SELECT inicio_vigencia, perc_fornecedor, perc_entidade, perc_admin
        FROM percentuais_vigencia ORDER BY inicio_vigencia ASC
    """)
    vigencias_asc = cursor.fetchall()

    fechamento_pendente          = _calcular_fechamento_pendente(cursor, vigencias_asc, 'fornecedor')
    fechamento_entidade_pendente = _calcular_fechamento_pendente(cursor, vigencias_asc, 'entidade')
    fechamento_admin_pendente    = _calcular_fechamento_pendente(cursor, vigencias_asc, 'admin')

    # ---- Histórico de Faturamentos — Fornecedor ----
    cursor.execute("""
        SELECT fat.id, fat.fornecedor_id, fat.mes_ref, fat.numero_baixa,
               fat.quantidade, fat.valor_bruto, fat.perc_fornecedor,
               fat.valor_liquido, fat.data_faturamento, f.razao_social
        FROM faturamentos fat
        JOIN fornecedores f ON f.id = fat.fornecedor_id
        ORDER BY fat.mes_ref DESC, f.razao_social
    """)
    faturamentos_hist = cursor.fetchall()

    # ---- Histórico de Faturamentos — Entidade Favorecida ----
    cursor.execute("""
        SELECT fat.id, fat.fornecedor_id, fat.mes_ref, fat.numero_baixa,
               fat.quantidade, fat.valor_bruto, fat.perc_entidade,
               fat.valor_liquido, fat.data_faturamento, f.razao_social
        FROM faturamentos_entidade fat
        JOIN fornecedores f ON f.id = fat.fornecedor_id
        ORDER BY fat.mes_ref DESC, f.razao_social
    """)
    faturamentos_entidade_hist = cursor.fetchall()

    # ---- Histórico de Faturamentos — Administração ----
    cursor.execute("""
        SELECT fat.id, fat.fornecedor_id, fat.mes_ref, fat.numero_baixa,
               fat.quantidade, fat.valor_bruto, fat.perc_admin,
               fat.valor_liquido, fat.data_faturamento, f.razao_social
        FROM faturamentos_admin fat
        JOIN fornecedores f ON f.id = fat.fornecedor_id
        ORDER BY fat.mes_ref DESC, f.razao_social
    """)
    faturamentos_admin_hist = cursor.fetchall()

    conn.close()

    return render_template("admin_painel.html",
                           fornecedores=fornecedores,
                           usuarios=usuarios,
                           plantios=plantios,
                           compras=compras,
                           especies=especies,
                           dados_bancarios=dados_bancarios,
                           dados_bancarios_entidade=dados_bancarios_entidade,
                           entidades=entidades,
                           percentuais=percentuais,
                           fechamento_pendente=fechamento_pendente,
                           fechamento_entidade_pendente=fechamento_entidade_pendente,
                           fechamento_admin_pendente=fechamento_admin_pendente,
                           faturamentos_hist=faturamentos_hist,
                           faturamentos_entidade_hist=faturamentos_entidade_hist,
                           faturamentos_admin_hist=faturamentos_admin_hist,
                           busca=busca,
                           tipo=tipo)


# ===================== ROTA ADMIN: SALVAR / EDITAR FORNECEDOR =====================
# Permite ao administrador atualizar todos os campos do fornecedor.
# Acesso restrito ao administrador (session["admin"]).
@app.route("/admin/fornecedor/<int:fid>/salvar", methods=["POST"])
def admin_salvar_fornecedor(fid):
    if not session.get("admin"):
        return redirect("/admin/login")

    razao_social = request.form.get("razao_social", "").strip()
    cnpj         = request.form.get("cnpj", "").strip()
    whatsapp     = request.form.get("whatsapp", "").strip()
    cidade       = request.form.get("cidade", "").strip()
    uf           = request.form.get("uf", "").strip().upper()
    tipo_planta  = request.form.get("tipo_planta", "").strip()
    maps_link    = request.form.get("maps_link", "").strip()
    email        = request.form.get("email", "").strip()

    conn   = get_db()
    cursor = conn.cursor()

    # Atualiza todos os campos editáveis do fornecedor — senha não é alterada aqui
    cursor.execute("""
        UPDATE fornecedores
        SET razao_social = ?, cnpj = ?, whatsapp = ?, cidade = ?, uf = ?,
            tipo_planta = ?, maps_link = ?, email = ?
        WHERE id = ?
    """, (razao_social, cnpj, whatsapp, cidade, uf, tipo_planta, maps_link, email, fid))
    conn.commit()
    conn.close()

    flash("Fornecedor atualizado com sucesso.", "sucesso")
    return redirect("/admin/painel?tipo=fornecedores")


# ===================== ROTA ADMIN: EXCLUIR FORNECEDOR =====================
# Remove permanentemente o fornecedor do banco de dados.
# Acesso restrito ao administrador (session["admin"]).
@app.route("/admin/fornecedor/<int:fid>/excluir", methods=["POST"])
def admin_excluir_fornecedor(fid):
    if not session.get("admin"):
        return redirect("/admin/login")

    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM fornecedores WHERE id = ?", (fid,))
    conn.commit()
    conn.close()

    flash("Fornecedor excluído com sucesso.", "sucesso")
    return redirect("/admin/painel?tipo=fornecedores")


# ===================== ROTA ADMIN: INATIVAR / ATIVAR FORNECEDOR =====================
# Alterna o status ativo (1 = ativo, 0 = inativo) do fornecedor.
# Fornecedores inativos não aparecem na rota /plantio/credenciado.
# Acesso restrito ao administrador (session["admin"]).
@app.route("/admin/fornecedor/<int:fid>/inativar", methods=["POST"])
def admin_inativar_fornecedor(fid):
    if not session.get("admin"):
        return redirect("/admin/login")

    conn   = get_db()
    cursor = conn.cursor()

    # Lê o status atual e inverte: 1 → 0 (inativar) ou 0 → 1 (reativar)
    cursor.execute("SELECT ativo FROM fornecedores WHERE id = ?", (fid,))
    row = cursor.fetchone()
    if row:
        novo_status = 0 if (row[0] == 1 or row[0] is None) else 1
        cursor.execute("UPDATE fornecedores SET ativo = ? WHERE id = ?", (novo_status, fid))
        conn.commit()
        msg = "Fornecedor inativado." if novo_status == 0 else "Fornecedor reativado."
        flash(msg, "sucesso")
    conn.close()

    return redirect("/admin/painel?tipo=fornecedores")


# ===================== ROTA MEU PERFIL =====================
# GET:  exibe os dados atuais do usuário logado para edição.
# POST: salva as alterações de nome, email, cpf, telefone e data de nascimento.
#       A senha NÃO é alterada aqui — usa fluxo separado via token por email.
@app.route("/meu-perfil", methods=["GET", "POST"])
def meu_perfil():
    if "usuario_id" not in session:
        return redirect("/login")

    conn   = get_db()
    cursor = conn.cursor()

    if request.method == "POST":
        nome                = request.form.get("nome", "").strip()
        email               = request.form.get("email", "").strip()
        cpf                 = request.form.get("cpf", "").strip()
        telefone            = request.form.get("telefone", "").strip()
        data_nascimento     = request.form.get("data_nascimento", "").strip()
        # Campos de localização, entidade e CNPJs (podem ser vazios para usuários antigos)
        uf     = request.form.get("uf", "").strip()
        cidade = request.form.get("cidade", "").strip()

        try:
            # Atualiza os dados cadastrais incluindo localização.
            # A senha permanece intocada — alteração segue fluxo separado via token.
            cursor.execute("""
                UPDATE usuarios
                SET nome = ?, email = ?, cpf = ?, telefone = ?, data_nascimento = ?,
                    uf = ?, cidade = ?
                WHERE id = ?
            """, (nome, email, cpf, telefone, data_nascimento,
                  uf, cidade,
                  session["usuario_id"]))
            conn.commit()
            # Atualiza o nome na sessão para refletir imediatamente no dashboard
            session["usuario_nome"] = nome
            flash("Dados atualizados com sucesso.", "sucesso")
        except:
            # Email duplicado (único no banco)
            flash("Este email já está em uso por outro cadastro.", "erro")
        finally:
            conn.close()

        return redirect("/meu-perfil")

    # GET: busca os dados atuais do usuário incluindo localização.
    # u[0]=id  u[1]=nome  u[2]=email  u[3]=cpf
    # u[4]=telefone  u[5]=data_nascimento  u[6]=uf  u[7]=cidade
    cursor.execute("""
        SELECT id, nome, email, cpf, telefone, data_nascimento, uf, cidade
        FROM usuarios WHERE id = ?
    """, (session["usuario_id"],))
    usuario = cursor.fetchone()
    conn.close()

    return render_template("meu_perfil.html", usuario=usuario)


# ===================== ROTA MEU PERFIL: SOLICITAR TROCA DE SENHA =====================
# Gera um token único, salva em reset_tokens e envia um email ao usuário
# com o link para redefinir a senha — mesmo mecanismo do esqueci-senha.
@app.route("/meu-perfil/solicitar-senha", methods=["POST"])
def perfil_solicitar_senha():
    if "usuario_id" not in session:
        return redirect("/login")

    conn   = get_db()
    cursor = conn.cursor()

    # Busca o email do usuário logado
    cursor.execute("SELECT email FROM usuarios WHERE id = ?", (session["usuario_id"],))
    row = cursor.fetchone()

    if row:
        email = row[0]
        token = secrets.token_urlsafe(32)
        cursor.execute(
            "INSERT INTO reset_tokens (email, token, usado) VALUES (?, ?, 0)",
            (email, token)
        )
        conn.commit()
        conn.close()

        # Monta o link e o email HTML de redefinição de senha
        base_url   = os.environ.get("APP_URL", "http://127.0.0.1:5000")
        link       = f"{base_url}/redefinir-senha/{token}"
        corpo_html = f"""
        <div style="font-family:Arial,sans-serif;max-width:480px;margin:auto;padding:24px;border:1px solid #d1fae5;border-radius:12px;">
            <h2 style="color:#166534;">🌱 Plantando Vida — Alteração de Senha</h2>
            <p>Você solicitou a alteração de senha da sua conta.</p>
            <p>Clique no botão abaixo para criar uma nova senha:</p>
            <a href="{link}" style="display:inline-block;margin:16px 0;padding:12px 24px;background:#16a34a;color:#fff;border-radius:8px;text-decoration:none;font-weight:bold;">
                Alterar minha senha
            </a>
            <p style="color:#6b7280;font-size:12px;">Se você não solicitou isso, ignore este email. O link expira após o uso.</p>
        </div>
        """

        enviou = enviar_email(email, "Alteração de Senha — Plantando Vida", corpo_html)

        if enviou:
            flash(f"Link enviado para {email}. Verifique sua caixa de entrada.", "sucesso")
        else:
            # EMAIL_SENHA não configurada: exibe o link diretamente (modo desenvolvimento)
            flash(f"Email não configurado. Link de redefinição: {link}", "erro")
    else:
        conn.close()
        flash("Usuário não encontrado.", "erro")

    return redirect("/meu-perfil")


# ===================== ROTA ADMIN: CADASTRAR ESPÉCIE DE PLANTA =====================
# Cria um novo registro na tabela especies_plantas.
# Acesso restrito ao administrador (session["admin"]).
@app.route("/admin/especie/cadastrar", methods=["POST"])
def admin_cadastrar_especie():
    if not session.get("admin"):
        return redirect("/admin/login")

    nome      = request.form.get("nome", "").strip()
    tipo      = request.form.get("tipo", "Nativa")
    valor_str = request.form.get("valor", "50").replace(",", ".")

    if not nome:
        flash("Informe o nome da planta.", "erro")
        return redirect("/admin/painel?tipo=especies")

    try:
        valor = float(valor_str)
    except ValueError:
        valor = 50.0

    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO especies_plantas (nome, tipo, valor) VALUES (?, ?, ?)",
        (nome, tipo, valor)
    )
    conn.commit()
    conn.close()

    flash(f"Espécie '{nome}' cadastrada com sucesso.", "sucesso")
    return redirect("/admin/painel?tipo=especies")


# ===================== ROTA ADMIN: EDITAR ESPÉCIE =====================
@app.route("/admin/especie/<int:eid>/editar", methods=["POST"])
def admin_editar_especie(eid):
    if not session.get("admin"):
        return redirect("/admin/login")

    nome      = request.form.get("nome", "").strip()
    tipo      = request.form.get("tipo", "Nativa")
    valor_str = request.form.get("valor", "50").replace(",", ".")

    try:
        valor = float(valor_str)
    except ValueError:
        valor = 50.0

    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE especies_plantas SET nome = ?, tipo = ?, valor = ? WHERE id = ?",
        (nome, tipo, valor, eid)
    )
    conn.commit()
    conn.close()

    flash("Espécie atualizada com sucesso.", "sucesso")
    return redirect("/admin/painel?tipo=especies")


# ===================== ROTA ADMIN: EXCLUIR ESPÉCIE =====================
@app.route("/admin/especie/<int:eid>/excluir", methods=["POST"])
def admin_excluir_especie(eid):
    if not session.get("admin"):
        return redirect("/admin/login")

    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM especies_plantas WHERE id = ?", (eid,))
    conn.commit()
    conn.close()

    flash("Espécie removida.", "sucesso")
    return redirect("/admin/painel?tipo=especies")


# ===================== ROTA ADMIN: IMPORTAR ESPÉCIES VIA EXCEL =====================
# Lê um arquivo .xlsx com colunas: Nome da Planta | Tipo | Valor
# Ignora a primeira linha (cabeçalho) e duplicatas já existentes no banco.
@app.route("/admin/importar-especies", methods=["POST"])
def admin_importar_especies():
    if not session.get("admin"):
        return redirect("/admin/login")

    arquivo = request.files.get("arquivo_xls")
    if not arquivo or arquivo.filename == "":
        flash("Selecione um arquivo Excel (.xlsx).", "erro")
        return redirect("/admin/painel?tipo=especies")

    try:
        import openpyxl
        wb = openpyxl.load_workbook(arquivo, read_only=True)
        ws = wb.active

        conn   = get_db()
        cursor = conn.cursor()
        importados = 0

        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i == 0:
                continue  # Pula o cabeçalho

            # Coluna A: Nome (obrigatório)
            nome = str(row[0]).strip() if row[0] else ""
            if not nome or nome.lower() == "none":
                continue

            # Coluna B: Tipo (opcional, padrão Nativa)
            tipo = str(row[1]).strip() if len(row) > 1 and row[1] else "Nativa"
            if tipo not in ("Frutífera", "Nativa"):
                tipo = "Nativa"

            # Coluna C: Valor (opcional, padrão 50.0)
            try:
                valor = float(str(row[2]).replace(",", ".")) if len(row) > 2 and row[2] else 50.0
            except (ValueError, AttributeError):
                valor = 50.0

            # Evita duplicatas pelo nome
            cursor.execute("SELECT id FROM especies_plantas WHERE nome = ?", (nome,))
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO especies_plantas (nome, tipo, valor) VALUES (?, ?, ?)",
                    (nome, tipo, valor)
                )
                importados += 1

        conn.commit()
        conn.close()
        flash(f"{importados} espécie(s) importada(s) com sucesso.", "sucesso")

    except Exception as e:
        flash(f"Erro ao importar arquivo: {str(e)}", "erro")

    return redirect("/admin/painel?tipo=especies")


# ===================== ROTA ADMIN: SALVAR DADOS BANCÁRIOS =====================
# Upsert: atualiza o registro existente ou cria um novo (registro único).
# O QR Code PIX é salvo em static/pix/ se enviado.
@app.route("/admin/dados-bancarios/salvar", methods=["POST"])
def admin_salvar_dados_bancarios():
    if not session.get("admin"):
        return redirect("/admin/login")

    nome_empresarial = request.form.get("nome_empresarial", "").strip()
    banco            = request.form.get("banco", "").strip()
    conta            = request.form.get("conta", "").strip()
    agencia          = request.form.get("agencia", "").strip()
    chave_pix        = request.form.get("chave_pix", "").strip()

    # Upload do QR Code PIX — salvo como pix_qrcode.{ext} em static/pix/
    os.makedirs(PIX_FOLDER, exist_ok=True)
    qrcode_novo = None
    arquivo_qr  = request.files.get("qrcode_pix")
    if arquivo_qr and arquivo_qr.filename:
        ext = arquivo_qr.filename.rsplit(".", 1)[-1].lower()
        if ext in {"jpg", "jpeg", "png"}:
            nome_arquivo = f"pix_qrcode.{ext}"
            arquivo_qr.save(os.path.join(PIX_FOLDER, nome_arquivo))
            qrcode_novo = nome_arquivo

    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, qrcode_pix FROM dados_bancarios LIMIT 1")
    existente = cursor.fetchone()

    # Mantém o QR Code anterior se nenhum novo foi enviado
    qrcode_final = qrcode_novo or (existente[1] if existente else None)

    if existente:
        cursor.execute("""
            UPDATE dados_bancarios
            SET nome_empresarial=?, banco=?, conta=?, agencia=?, chave_pix=?, qrcode_pix=?
            WHERE id=?
        """, (nome_empresarial, banco, conta, agencia, chave_pix, qrcode_final, existente[0]))
    else:
        cursor.execute("""
            INSERT INTO dados_bancarios (nome_empresarial, banco, conta, agencia, chave_pix, qrcode_pix)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (nome_empresarial, banco, conta, agencia, chave_pix, qrcode_final))

    conn.commit()
    conn.close()

    flash("Dados bancários salvos com sucesso.", "sucesso")
    return redirect("/admin/painel?tipo=dados_bancarios")


# ===================== ROTA ADMIN: SALVAR DADOS BANCÁRIOS DA ENTIDADE FAVORECIDA =====================
# Upsert: atualiza o registro existente ou cria um novo (registro único).
# O QR Code PIX é salvo em static/pix/ com prefixo 'entidade_' para não colidir.
@app.route("/admin/dados-bancarios-entidade/salvar", methods=["POST"])
def admin_salvar_dados_bancarios_entidade():
    if not session.get("admin"):
        return redirect("/admin/login")

    nome_empresarial = request.form.get("nome_empresarial_ent", "").strip()
    banco            = request.form.get("banco_ent", "").strip()
    conta            = request.form.get("conta_ent", "").strip()
    agencia          = request.form.get("agencia_ent", "").strip()
    chave_pix        = request.form.get("chave_pix_ent", "").strip()

    # Upload do QR Code — salvo como entidade_qrcode.{ext} em static/pix/
    os.makedirs(PIX_FOLDER, exist_ok=True)
    qrcode_novo = None
    arquivo_qr  = request.files.get("qrcode_pix_ent")
    if arquivo_qr and arquivo_qr.filename:
        ext = arquivo_qr.filename.rsplit(".", 1)[-1].lower()
        if ext in {"jpg", "jpeg", "png"}:
            nome_arquivo = f"entidade_qrcode.{ext}"
            arquivo_qr.save(os.path.join(PIX_FOLDER, nome_arquivo))
            qrcode_novo = nome_arquivo

    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, qrcode_pix FROM dados_bancarios_entidade LIMIT 1")
    existente = cursor.fetchone()

    # Mantém o QR Code anterior se nenhum novo foi enviado
    qrcode_final = qrcode_novo or (existente[1] if existente else None)

    if existente:
        cursor.execute("""
            UPDATE dados_bancarios_entidade
            SET nome_empresarial=?, banco=?, conta=?, agencia=?, chave_pix=?, qrcode_pix=?
            WHERE id=?
        """, (nome_empresarial, banco, conta, agencia, chave_pix, qrcode_final, existente[0]))
    else:
        cursor.execute("""
            INSERT INTO dados_bancarios_entidade (nome_empresarial, banco, conta, agencia, chave_pix, qrcode_pix)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (nome_empresarial, banco, conta, agencia, chave_pix, qrcode_final))

    conn.commit()
    conn.close()

    flash("Dados bancários da entidade salvos com sucesso.", "sucesso")
    return redirect("/admin/painel?tipo=fechamento_entidade")


# ===================== ROTA ADMIN: SALVAR ENTIDADE FAVORECIDA =====================
# Insere uma nova entidade na tabela entidades.
# Acesso restrito ao administrador (session["admin"]).
@app.route("/admin/entidade/salvar", methods=["POST"])
def admin_salvar_entidade():
    if not session.get("admin"):
        return redirect("/admin/login")

    razao_social = request.form.get("razao_social", "").strip()
    cnpj         = request.form.get("cnpj", "").strip()
    whatsapp     = request.form.get("whatsapp", "").strip()

    if not razao_social:
        flash("O campo Razão Social é obrigatório.", "erro")
        return redirect("/admin/painel?tipo=fechamento_entidade")

    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO entidades (razao_social, cnpj, whatsapp) VALUES (?, ?, ?)",
        (razao_social, cnpj, whatsapp)
    )
    conn.commit()
    conn.close()

    flash("Entidade cadastrada com sucesso.", "sucesso")
    return redirect("/admin/painel?tipo=fechamento_entidade")


# ===================== ROTA ADMIN: EXCLUIR ENTIDADE FAVORECIDA =====================
# Remove permanentemente a entidade do banco.
# Acesso restrito ao administrador (session["admin"]).
@app.route("/admin/entidade/<int:eid>/excluir", methods=["POST"])
def admin_excluir_entidade(eid):
    if not session.get("admin"):
        return redirect("/admin/login")

    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM entidades WHERE id = ?", (eid,))
    conn.commit()
    conn.close()

    flash("Entidade excluída.", "sucesso")
    return redirect("/admin/painel?tipo=fechamento_entidade")


# ===================== API: BUSCA DE ESPÉCIES (AJAX) =====================
# Retorna JSON com espécies filtradas por tipo e/ou busca por nome.
# Usada pelo modal de compra em /plantio/credenciado via fetch().
@app.route("/api/plantas")
def api_plantas():
    if "usuario_id" not in session:
        return jsonify({"plantas": []})

    tipo  = request.args.get("tipo", "").strip()
    busca = request.args.get("busca", "").strip()

    conn   = get_db()
    cursor = conn.cursor()

    # Monta a query dinamicamente conforme os filtros recebidos.
    # LOWER() + LIKE no campo tipo garante tolerância a variações de capitalização
    # caso o banco contenha valores levemente diferentes do canônico.
    if tipo and busca:
        cursor.execute(
            "SELECT id, nome, tipo, valor FROM especies_plantas WHERE LOWER(tipo) LIKE LOWER(?) AND nome LIKE ? ORDER BY nome",
            (f"%{tipo}%", f"%{busca}%")
        )
    elif tipo:
        cursor.execute(
            "SELECT id, nome, tipo, valor FROM especies_plantas WHERE LOWER(tipo) LIKE LOWER(?) ORDER BY nome",
            (f"%{tipo}%",)
        )
    elif busca:
        cursor.execute(
            "SELECT id, nome, tipo, valor FROM especies_plantas WHERE nome LIKE ? ORDER BY nome",
            (f"%{busca}%",)
        )
    else:
        # Sem filtros: retorna as primeiras 30 para não sobrecarregar
        cursor.execute("SELECT id, nome, tipo, valor FROM especies_plantas ORDER BY nome LIMIT 30")

    plantas = [{"id": r[0], "nome": r[1], "tipo": r[2], "valor": r[3]} for r in cursor.fetchall()]
    conn.close()
    return jsonify({"plantas": plantas})


# ===================== ROTA FINALIZAR COMPRA =====================
# Salva a compra na tabela compras e envia email de confirmação ao usuário.
# Acesso restrito a usuários logados.
@app.route("/compra/finalizar", methods=["POST"])
def compra_finalizar():
    if "usuario_id" not in session:
        return redirect("/login")

    fornecedor_id = request.form.get("fornecedor_id") or None
    especie_nome  = request.form.get("especie_nome", "").strip()
    tipo_planta   = request.form.get("tipo_planta", "").strip()
    valor_str     = request.form.get("valor", "0").replace(",", ".")

    if not especie_nome or not tipo_planta:
        flash("Selecione uma planta antes de finalizar a compra.", "erro")
        return redirect("/plantio/credenciado")

    try:
        valor = float(valor_str)
    except ValueError:
        valor = 0.0

    # Salva o comprovante de pagamento em static/uploads/
    comprovante = salvar_foto("comprovante")

    conn   = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO compras (usuario_id, fornecedor_id, especie_nome, tipo_planta, valor, comprovante, status)
        VALUES (?, ?, ?, ?, ?, ?, 'em_analise')
    """, (session["usuario_id"], fornecedor_id, especie_nome, tipo_planta, valor, comprovante))
    conn.commit()

    # Recupera o ID da compra recém-inserida para uso no número do pedido
    cursor.execute(
        "SELECT id FROM compras WHERE usuario_id=? ORDER BY id DESC LIMIT 1",
        (session["usuario_id"],)
    )
    compra_id = cursor.fetchone()[0]

    # Busca nome e email do usuário para envio do email de confirmação
    cursor.execute("SELECT nome, email FROM usuarios WHERE id = ?", (session["usuario_id"],))
    usuario = cursor.fetchone()
    conn.close()

    # Envia email de confirmação ao usuário
    if usuario:
        nome_usuario, email_usuario = usuario
        corpo_html = f"""
        <div style="font-family:sans-serif;max-width:520px;margin:auto;background:#f0fdf4;
                    border-radius:12px;padding:32px;border:1px solid #bbf7d0">
            <div style="text-align:center;margin-bottom:24px">
                <span style="font-size:48px">🌱</span>
                <h1 style="color:#15803d;font-size:20px;margin:12px 0 4px">Compra Recebida!</h1>
                <p style="color:#4b7a58;font-size:14px;margin:0">
                    Olá, {nome_usuario}! Recebemos sua solicitação de compra.
                </p>
            </div>
            <div style="background:#ffffff;border-radius:8px;padding:16px;
                        border-left:4px solid #16a34a;margin-bottom:20px">
                <p style="margin:0 0 6px;font-size:13px;color:#6b7280">Detalhes da compra:</p>
                <p style="margin:4px 0;font-size:14px;color:#111827">
                    🌿 <strong>Espécie:</strong> {especie_nome}
                </p>
                <p style="margin:4px 0;font-size:14px;color:#111827">
                    {'🍎' if tipo_planta == 'Frutífera' else '🌳'} <strong>Tipo:</strong> {tipo_planta}
                </p>
                <p style="margin:4px 0;font-size:14px;color:#111827">
                    💰 <strong>Valor:</strong> R$ {valor:.2f}
                </p>
            </div>
            <div style="background:#fefce8;border-radius:8px;padding:14px;
                        border-left:4px solid #ca8a04;margin-bottom:20px">
                <p style="margin:0;font-size:14px;color:#854d0e;font-weight:bold">
                    ⏳ Aguarde a validação do seu pagamento.
                </p>
                <p style="margin:6px 0 0;font-size:13px;color:#92400e">
                    Nossa equipe irá conferir o comprovante e confirmar sua compra em breve.
                    Você será notificado por email assim que o pagamento for validado.
                </p>
            </div>
            <div style="text-align:center;margin-top:20px">
                <a href="{os.environ.get('APP_URL', 'http://localhost:5000')}/plantios/pendentes"
                   style="background:#16a34a;color:#ffffff;padding:10px 24px;border-radius:8px;
                          text-decoration:none;font-weight:bold;font-size:14px">
                    Acompanhar minha compra
                </a>
            </div>
            <p style="font-size:11px;color:#9ca3af;text-align:center;margin-top:24px">
                Plantando Vida — juntos por um mundo mais verde 🌱
            </p>
        </div>
        """
        enviar_email(email_usuario, "🌱 Compra recebida — Aguardando validação | Plantando Vida", corpo_html)

    # Exibe o número sequencial do pedido para rastreamento
    flash(
        f"Pedido #{compra_id:06d} registrado com sucesso! "
        f"Acompanhe a aprovação em 'Meus Plantios'.",
        "sucesso"
    )
    return redirect("/plantios/pendentes")


# ===================== ROTA VOUCHER DE RETIRADA =====================
# Gera um voucher em QR Code para a compra aprovada.
# Segurança:
#   - Somente o dono da compra pode acessar (usuario_id da sessão).
#   - Somente compras com status='aprovado' geram o voucher.
#   - Compras em análise ou reprovadas retornam erro e redirecionam.
@app.route("/voucher/<int:compra_id>")
def voucher(compra_id):
    if "usuario_id" not in session:
        return redirect("/login")

    conn   = get_db()
    cursor = conn.cursor()

    # Busca a compra verificando posse (usuario_id) e status aprovado
    cursor.execute("""
        SELECT c.id, c.especie_nome, c.tipo_planta, c.valor, c.criado_em,
               u.nome,
               COALESCE(f.razao_social, 'Não informado') AS fornecedor_nome,
               COALESCE(f.cidade, '')    AS cidade,
               COALESCE(f.uf, '')        AS uf,
               COALESCE(f.whatsapp, '')  AS whatsapp
        FROM compras c
        JOIN   usuarios    u ON u.id = c.usuario_id
        LEFT JOIN fornecedores f ON f.id = c.fornecedor_id
        WHERE c.id = ? AND c.usuario_id = ? AND c.status = 'aprovado'
    """, (compra_id, session["usuario_id"]))
    compra = cursor.fetchone()
    conn.close()

    # Voucher indisponível: compra não encontrada, não pertence ao usuário ou não aprovada
    if not compra:
        flash("Voucher indisponível. O pagamento precisa ser aprovado pelo administrador.", "erro")
        return redirect("/plantios/pendentes")

    # Monta o texto do QR Code com todas as informações relevantes para o fornecedor
    qr_texto = (
        f"PLANTANDO VIDA — VOUCHER DE RETIRADA\n"
        f"Pedido: #{compra[0]:06d}\n"
        f"Planta: {compra[1]}\n"
        f"Tipo: {compra[2]}\n"
        f"Valor: R$ {compra[3]:.2f}\n"
        f"Fornecedor: {compra[6]} — {compra[7]}/{compra[8]}\n"
        f"Comprador: {compra[5]}\n"
        f"Data: {compra[4]}"
    )

    # Gera o QR Code como imagem PNG em memória e codifica em base64
    # para incorporar diretamente no HTML sem gravar arquivo no disco
    import io, base64
    qr = qrcode.QRCode(version=1, box_size=8, border=4,
                       error_correction=qrcode.constants.ERROR_CORRECT_H)
    qr.add_data(qr_texto)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#166534", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_base64 = base64.b64encode(buf.getvalue()).decode()

    return render_template("voucher.html", compra=compra, qr_base64=qr_base64)


# ===================== ROTA ADMIN: APROVAR COMPRA =====================
@app.route("/admin/compra/<int:cid>/aprovar", methods=["POST"])
def admin_aprovar_compra(cid):
    if not session.get("admin"):
        return redirect("/admin/login")

    conn   = get_db()
    cursor = conn.cursor()

    # Busca dados para o email antes de atualizar
    cursor.execute("""
        SELECT c.especie_nome, c.tipo_planta, c.valor, u.nome, u.email
        FROM compras c JOIN usuarios u ON u.id = c.usuario_id
        WHERE c.id = ?
    """, (cid,))
    dados = cursor.fetchone()

    cursor.execute("UPDATE compras SET status = 'aprovado' WHERE id = ?", (cid,))
    conn.commit()
    conn.close()

    # Notifica o usuário por email
    if dados:
        especie, tipo, valor, nome_usuario, email_usuario = dados
        corpo_html = f"""
        <div style="font-family:sans-serif;max-width:520px;margin:auto;background:#f0fdf4;
                    border-radius:12px;padding:32px;border:1px solid #bbf7d0">
            <div style="text-align:center;margin-bottom:20px">
                <span style="font-size:48px">✅</span>
                <h1 style="color:#15803d;font-size:20px;margin:12px 0 4px">Pagamento Aprovado!</h1>
                <p style="color:#4b7a58;font-size:14px">Olá, {nome_usuario}! Seu pagamento foi validado.</p>
            </div>
            <div style="background:#fff;border-radius:8px;padding:16px;border-left:4px solid #16a34a;margin-bottom:16px">
                <p style="margin:4px 0;font-size:14px;color:#111827">🌿 <strong>{especie}</strong> — {tipo}</p>
                <p style="margin:4px 0;font-size:14px;color:#111827">💰 R$ {valor:.2f}</p>
            </div>
            <p style="font-size:13px;color:#4b5563;text-align:center">
                Sua muda já pode ser retirada no fornecedor credenciado. Bom plantio! 🌱
            </p>
        </div>
        """
        enviar_email(email_usuario, "✅ Pagamento aprovado — Plantando Vida", corpo_html)

    flash("Compra aprovada.", "sucesso")
    return redirect("/admin/painel?tipo=plantios")


# ===================== ROTA ADMIN: REPROVAR COMPRA =====================
@app.route("/admin/compra/<int:cid>/reprovar", methods=["POST"])
def admin_reprovar_compra(cid):
    if not session.get("admin"):
        return redirect("/admin/login")

    conn   = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT c.especie_nome, c.tipo_planta, c.valor, u.nome, u.email
        FROM compras c JOIN usuarios u ON u.id = c.usuario_id
        WHERE c.id = ?
    """, (cid,))
    dados = cursor.fetchone()

    cursor.execute("UPDATE compras SET status = 'reprovado' WHERE id = ?", (cid,))
    conn.commit()
    conn.close()

    if dados:
        especie, tipo, valor, nome_usuario, email_usuario = dados
        corpo_html = f"""
        <div style="font-family:sans-serif;max-width:520px;margin:auto;background:#fff7f7;
                    border-radius:12px;padding:32px;border:1px solid #fecaca">
            <div style="text-align:center;margin-bottom:20px">
                <span style="font-size:48px">❌</span>
                <h1 style="color:#b91c1c;font-size:20px;margin:12px 0 4px">Pagamento Não Confirmado</h1>
                <p style="color:#7f5252;font-size:14px">Olá, {nome_usuario}. Não foi possível confirmar seu pagamento.</p>
            </div>
            <div style="background:#fff;border-radius:8px;padding:16px;border-left:4px solid #ef4444;margin-bottom:16px">
                <p style="margin:4px 0;font-size:14px;color:#111827">🌿 <strong>{especie}</strong> — {tipo}</p>
                <p style="margin:4px 0;font-size:14px;color:#111827">💰 R$ {valor:.2f}</p>
            </div>
            <p style="font-size:13px;color:#4b5563;text-align:center">
                Entre em contato conosco para mais informações ou tente novamente.
            </p>
        </div>
        """
        enviar_email(email_usuario, "❌ Pagamento não confirmado — Plantando Vida", corpo_html)

    flash("Compra reprovada.", "sucesso")
    return redirect("/admin/painel?tipo=plantios")


# ===================== ROTA ADMIN: DEIXAR COMPRA EM ANÁLISE =====================
@app.route("/admin/compra/<int:cid>/analise", methods=["POST"])
def admin_analise_compra(cid):
    if not session.get("admin"):
        return redirect("/admin/login")

    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE compras SET status = 'em_analise' WHERE id = ?", (cid,))
    conn.commit()
    conn.close()

    flash("Compra retornada para análise.", "sucesso")
    return redirect("/admin/painel?tipo=plantios")


# ===================== HELPER: CALCULAR PERCENTUAL VIGENTE =====================
# Recebe uma data e a lista de vigências ordenada ASC por inicio_vigencia.
# campo_idx: índice do percentual desejado na tupla de vigência.
#   1 = perc_fornecedor  2 = perc_entidade  3 = perc_admin
# Retorna o percentual aplicável naquela data. Default 100% se sem regra.
def _perc_vigente(data_str, vigencias, campo_idx=1):
    data = (data_str or "9999-12-31")[:10]
    resultado = 100.0
    for v in vigencias:              # v[0]=inicio_vigencia
        if v[0] <= data:
            resultado = v[campo_idx]
    return resultado


# ===================== HELPER: MONTAR FECHAMENTO PENDENTE (GENÉRICO) =====================
# Agrupa compras retiradas ainda não faturadas por (fornecedor, mês) e calcula valores.
# tipo: 'fornecedor' | 'entidade' | 'admin' — determina qual coluna e percentual usar.
# vigencias deve incluir todas as colunas: (inicio_vigencia, perc_forn, perc_ent, perc_adm).
def _calcular_fechamento_pendente(cursor, vigencias, tipo='fornecedor'):
    # Mapeia tipo → coluna de faturamento em compras e índice do percentual em vigencias
    _col = {
        'fornecedor': ('faturamento_id',          1),
        'entidade':   ('faturamento_entidade_id', 2),
        'admin':      ('faturamento_admin_id',    3),
    }
    fat_col, perc_idx = _col[tipo]

    cursor.execute(f"""
        SELECT c.id, c.fornecedor_id, c.valor, c.data_validacao,
               SUBSTR(COALESCE(c.data_validacao, ''), 1, 7) AS mes_ref,
               f.razao_social, f.whatsapp
        FROM compras c
        JOIN fornecedores f ON f.id = c.fornecedor_id
        WHERE c.status = 'retirado'
          AND (c.{fat_col} IS NULL OR c.{fat_col} = 0)
        ORDER BY c.fornecedor_id, mes_ref
    """)
    rows = cursor.fetchall()

    grupos = {}
    for row in rows:
        cid, forn_id, valor, data_valid, mes_ref, razao, whatsapp = row
        if not mes_ref:
            mes_ref = "sem_data"
        chave = (forn_id, mes_ref)

        if chave not in grupos:
            if mes_ref != "sem_data":
                ano, mes = mes_ref.split("-")
                numero_baixa = f"{mes}{ano}"
            else:
                numero_baixa = "000000"
            grupos[chave] = {
                "fornecedor_id": forn_id,
                "razao_social":  razao,
                "whatsapp":      whatsapp,
                "mes_ref":       mes_ref,
                "numero_baixa":  numero_baixa,
                "quantidade":    0,
                "valor_bruto":   0.0,
                "valor_liquido": 0.0,
                "percs":         set(),
            }

        perc = _perc_vigente(data_valid, vigencias, perc_idx)
        val  = valor or 0.0
        grupos[chave]["quantidade"]    += 1
        grupos[chave]["valor_bruto"]   += val
        grupos[chave]["valor_liquido"] += val * perc / 100
        grupos[chave]["percs"].add(perc)

    resultado = []
    for g in grupos.values():
        percs = sorted(g["percs"])
        g["perc_display"] = "/".join(f"{p:.0f}%" for p in percs)
        del g["percs"]
        resultado.append(g)

    resultado.sort(key=lambda x: (x["mes_ref"], x["razao_social"]))
    return resultado


# ===================== ROTA ADMIN: SALVAR PERCENTUAL DE VIGÊNCIA =====================
# Insere ou atualiza a tabela de percentuais de distribuição.
# Valida que a soma dos três percentuais seja exatamente 100%.
@app.route("/admin/percentual/salvar", methods=["POST"])
def admin_percentual_salvar():
    if not session.get("admin"):
        return redirect("/admin/login")

    inicio_vigencia = request.form.get("inicio_vigencia", "").strip()
    try:
        perc_fornecedor = float(request.form.get("perc_fornecedor", "0").replace(",", "."))
        perc_entidade   = float(request.form.get("perc_entidade",   "0").replace(",", "."))
        perc_admin      = float(request.form.get("perc_admin",      "0").replace(",", "."))
    except ValueError:
        flash("Percentuais inválidos. Use apenas números.", "erro")
        return redirect("/admin/painel?tipo=percentuais")

    # Valida que os três percentuais somam exatamente 100
    soma = round(perc_fornecedor + perc_entidade + perc_admin, 4)
    if abs(soma - 100.0) > 0.001:
        flash(f"A soma dos percentuais deve ser 100%. Soma atual: {soma:.2f}%", "erro")
        return redirect("/admin/painel?tipo=percentuais")

    if not inicio_vigencia:
        flash("Informe a data de início da vigência.", "erro")
        return redirect("/admin/painel?tipo=percentuais")

    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO percentuais_vigencia (inicio_vigencia, perc_fornecedor, perc_entidade, perc_admin)
        VALUES (?, ?, ?, ?)
    """, (inicio_vigencia, perc_fornecedor, perc_entidade, perc_admin))
    conn.commit()
    conn.close()

    flash(f"Vigência a partir de {inicio_vigencia} cadastrada com sucesso.", "sucesso")
    return redirect("/admin/painel?tipo=percentuais")


# ===================== ROTA ADMIN: EXCLUIR PERCENTUAL DE VIGÊNCIA =====================
@app.route("/admin/percentual/<int:pid>/excluir", methods=["POST"])
def admin_percentual_excluir(pid):
    if not session.get("admin"):
        return redirect("/admin/login")

    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM percentuais_vigencia WHERE id = ?", (pid,))
    conn.commit()
    conn.close()

    flash("Registro de vigência excluído.", "sucesso")
    return redirect("/admin/painel?tipo=percentuais")


# ===================== ROTA ADMIN: CRIAR FATURAMENTO (FECHAMENTO MENSAL) =====================
# Gera o fechamento mensal de um fornecedor para o mês informado.
# Segurança: recalcula tudo no servidor — não confia nos valores enviados pelo form.
# Fluxo:
#   1. Busca todas as compras retiradas não faturadas do fornecedor no mês.
#   2. Aplica o percentual vigente em cada compra individualmente.
#   3. Insere o registro em faturamentos e marca as compras com faturamento_id.
@app.route("/admin/faturamento/criar", methods=["POST"])
def admin_faturamento_criar():
    if not session.get("admin"):
        return redirect("/admin/login")

    fornecedor_id = request.form.get("fornecedor_id", "").strip()
    mes_ref       = request.form.get("mes_ref", "").strip()  # "YYYY-MM"

    if not fornecedor_id or not mes_ref:
        flash("Dados inválidos para faturamento.", "erro")
        return redirect("/admin/painel?tipo=fechamento")

    conn   = get_db()
    cursor = conn.cursor()

    # Busca vigências com todos os percentuais para cálculo por data
    cursor.execute("""
        SELECT inicio_vigencia, perc_fornecedor, perc_entidade, perc_admin
        FROM percentuais_vigencia ORDER BY inicio_vigencia ASC
    """)
    vigencias = cursor.fetchall()

    # Busca todas as compras pendentes deste fornecedor no mês especificado
    cursor.execute("""
        SELECT id, valor, data_validacao
        FROM compras
        WHERE fornecedor_id = ?
          AND status = 'retirado'
          AND (faturamento_id IS NULL OR faturamento_id = 0)
          AND SUBSTR(COALESCE(data_validacao, ''), 1, 7) = ?
    """, (int(fornecedor_id), mes_ref))
    compras_do_mes = cursor.fetchall()

    if not compras_do_mes:
        conn.close()
        flash("Nenhuma compra pendente para faturar neste período.", "erro")
        return redirect("/admin/painel?tipo=fechamento")

    # Recalcula totais com percentual vigente por data de cada compra
    quantidade   = len(compras_do_mes)
    valor_bruto  = 0.0
    valor_liquido = 0.0
    perc_usado   = 100.0  # percentual da última compra (referência para o registro)

    for row in compras_do_mes:
        _id, val, data_valid = row
        val       = val or 0.0
        perc      = _perc_vigente(data_valid, vigencias)
        valor_bruto   += val
        valor_liquido += val * perc / 100
        perc_usado    = perc  # guarda o último aplicado

    # Gera nº Baixa: "YYYY-MM" → "MMAAAA" (ex: "2026-04" → "042026")
    try:
        ano, mes = mes_ref.split("-")
        numero_baixa = f"{mes}{ano}"
    except ValueError:
        numero_baixa = mes_ref.replace("-", "")

    # Insere o faturamento e obtém o id gerado
    cursor.execute("""
        INSERT INTO faturamentos
            (fornecedor_id, mes_ref, numero_baixa, quantidade, valor_bruto, perc_fornecedor, valor_liquido)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (int(fornecedor_id), mes_ref, numero_baixa,
          quantidade, valor_bruto, perc_usado, valor_liquido))

    # Recupera o id do faturamento recém-inserido (compatível com SQLite e PostgreSQL)
    cursor.execute("""
        SELECT id FROM faturamentos
        WHERE fornecedor_id = ? AND mes_ref = ?
        ORDER BY id DESC LIMIT 1
    """, (int(fornecedor_id), mes_ref))
    fat_row      = cursor.fetchone()
    faturamento_id = fat_row[0] if fat_row else None

    # Marca cada compra com o id do faturamento gerado
    if faturamento_id:
        ids = [row[0] for row in compras_do_mes]
        for cid in ids:
            cursor.execute(
                "UPDATE compras SET faturamento_id = ? WHERE id = ?",
                (faturamento_id, cid)
            )

    conn.commit()
    conn.close()

    flash(
        f"Faturamento gerado — nº Baixa {numero_baixa} · "
        f"{quantidade} muda(s) · R$ {valor_liquido:.2f}",
        "sucesso"
    )
    return redirect("/admin/painel?tipo=fechamento")


# ===================== HELPER GENÉRICO: CRIAR FATURAMENTO POR TIPO =====================
# Centraliza a lógica de fechamento para entidade e admin, evitando duplicação.
# tipo: 'entidade' | 'admin'
# tabela: nome da tabela de destino ('faturamentos_entidade' | 'faturamentos_admin')
# perc_col: nome da coluna de percentual na tabela de destino
# fat_col: coluna de faturamento em compras que será marcada
# campo_idx: índice do percentual na tupla de vigências (2=entidade, 3=admin)
def _criar_faturamento_tipo(fornecedor_id, mes_ref, tipo, tabela, perc_col, fat_col, campo_idx):
    conn   = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT inicio_vigencia, perc_fornecedor, perc_entidade, perc_admin
        FROM percentuais_vigencia ORDER BY inicio_vigencia ASC
    """)
    vigencias = cursor.fetchall()

    cursor.execute(f"""
        SELECT id, valor, data_validacao FROM compras
        WHERE fornecedor_id = ?
          AND status = 'retirado'
          AND ({fat_col} IS NULL OR {fat_col} = 0)
          AND SUBSTR(COALESCE(data_validacao, ''), 1, 7) = ?
    """, (int(fornecedor_id), mes_ref))
    compras_do_mes = cursor.fetchall()

    if not compras_do_mes:
        conn.close()
        return False, "Nenhuma compra pendente para faturar neste período."

    quantidade    = len(compras_do_mes)
    valor_bruto   = 0.0
    valor_liquido = 0.0
    perc_usado    = 0.0

    for row in compras_do_mes:
        _id, val, data_valid = row
        val           = val or 0.0
        perc          = _perc_vigente(data_valid, vigencias, campo_idx)
        valor_bruto   += val
        valor_liquido += val * perc / 100
        perc_usado     = perc

    try:
        ano, mes    = mes_ref.split("-")
        numero_baixa = f"{mes}{ano}"
    except ValueError:
        numero_baixa = mes_ref.replace("-", "")

    cursor.execute(f"""
        INSERT INTO {tabela}
            (fornecedor_id, mes_ref, numero_baixa, quantidade, valor_bruto, {perc_col}, valor_liquido)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (int(fornecedor_id), mes_ref, numero_baixa,
          quantidade, valor_bruto, perc_usado, valor_liquido))

    cursor.execute(f"""
        SELECT id FROM {tabela} WHERE fornecedor_id = ? AND mes_ref = ?
        ORDER BY id DESC LIMIT 1
    """, (int(fornecedor_id), mes_ref))
    fat_row = cursor.fetchone()
    fat_id  = fat_row[0] if fat_row else None

    if fat_id:
        for row in compras_do_mes:
            cursor.execute(f"UPDATE compras SET {fat_col} = ? WHERE id = ?", (fat_id, row[0]))

    conn.commit()
    conn.close()
    return True, (f"nº Baixa {numero_baixa} · {quantidade} muda(s) · R$ {valor_liquido:.2f}")


# ===================== ROTA ADMIN: CRIAR FATURAMENTO — ENTIDADE FAVORECIDA =====================
@app.route("/admin/faturamento-entidade/criar", methods=["POST"])
def admin_faturamento_entidade_criar():
    if not session.get("admin"):
        return redirect("/admin/login")

    fornecedor_id = request.form.get("fornecedor_id", "").strip()
    mes_ref       = request.form.get("mes_ref", "").strip()

    if not fornecedor_id or not mes_ref:
        flash("Dados inválidos para faturamento.", "erro")
        return redirect("/admin/painel?tipo=fechamento_entidade")

    ok, msg = _criar_faturamento_tipo(
        fornecedor_id, mes_ref,
        tipo      = 'entidade',
        tabela    = 'faturamentos_entidade',
        perc_col  = 'perc_entidade',
        fat_col   = 'faturamento_entidade_id',
        campo_idx = 2
    )
    flash(f"Faturamento Entidade gerado — {msg}" if ok else msg, "sucesso" if ok else "erro")
    return redirect("/admin/painel?tipo=fechamento_entidade")


# ===================== ROTA ADMIN: CRIAR FATURAMENTO — ADMINISTRAÇÃO =====================
@app.route("/admin/faturamento-admin/criar", methods=["POST"])
def admin_faturamento_admin_criar():
    if not session.get("admin"):
        return redirect("/admin/login")

    fornecedor_id = request.form.get("fornecedor_id", "").strip()
    mes_ref       = request.form.get("mes_ref", "").strip()

    if not fornecedor_id or not mes_ref:
        flash("Dados inválidos para faturamento.", "erro")
        return redirect("/admin/painel?tipo=fechamento_admin")

    ok, msg = _criar_faturamento_tipo(
        fornecedor_id, mes_ref,
        tipo      = 'admin',
        tabela    = 'faturamentos_admin',
        perc_col  = 'perc_admin',
        fat_col   = 'faturamento_admin_id',
        campo_idx = 3
    )
    flash(f"Faturamento Administração gerado — {msg}" if ok else msg, "sucesso" if ok else "erro")
    return redirect("/admin/painel?tipo=fechamento_admin")


# ===================== ROTA INICIAR PLANTIO (5 ETAPAS) =====================
# Exibe a página com o fluxo guiado de 5 etapas para registrar o plantio definitivo.
# Acesso restrito ao dono da compra com status 'retirado' e sem plantio já vinculado.
@app.route("/plantio/iniciar/<int:compra_id>")
def plantio_iniciar(compra_id):
    if "usuario_id" not in session:
        return redirect("/login")

    conn   = get_db()
    cursor = conn.cursor()

    # Valida que a compra pertence ao usuário logado, está com status 'retirado'
    # e ainda não possui um plantio registrado (plantio_id nulo).
    cursor.execute("""
        SELECT c.id, c.especie_nome, c.tipo_planta, c.valor,
               COALESCE(f.razao_social, 'Fornecedor não informado'),
               f.cidade, f.uf, c.plantio_id
        FROM compras c
        LEFT JOIN fornecedores f ON f.id = c.fornecedor_id
        WHERE c.id = ? AND c.usuario_id = ? AND c.status = 'retirado'
    """, (compra_id, session["usuario_id"]))
    compra = cursor.fetchone()
    conn.close()

    # Se não encontrou a compra ou já tem plantio vinculado, redireciona
    if not compra:
        flash("Compra não encontrada ou ainda não retirada.", "erro")
        return redirect("/plantios/pendentes")

    if compra[7]:
        flash("Este plantio já foi registrado.", "sucesso")
        return redirect("/plantios/pendentes")

    return render_template("plantio_iniciar.html", compra=compra)


# ===================== ROTA CONCLUIR PLANTIO (POST DO FORMULÁRIO 5 ETAPAS) =====================
# Recebe os dados e fotos do formulário de 5 etapas, cria o registro em plantas_go
# e vincula o plantio à compra via compras.plantio_id.
@app.route("/plantio/concluir", methods=["POST"])
def plantio_concluir():
    if "usuario_id" not in session:
        return redirect("/login")

    compra_id   = request.form.get("compra_id", "").strip()
    especie     = request.form.get("especie", "").strip()
    tipo        = request.form.get("tipo", "").strip()
    municipio   = request.form.get("municipio", "").strip()
    bairro      = request.form.get("bairro", "").strip()
    latitude    = request.form.get("latitude", "").strip() or None
    longitude   = request.form.get("longitude", "").strip() or None
    qr_scan     = request.form.get("qr_scan", "").strip()

    # Validações básicas dos campos obrigatórios
    if not compra_id or not especie or not municipio or not bairro:
        flash("Preencha todos os campos obrigatórios.", "erro")
        return redirect("/plantios/pendentes")

    conn   = get_db()
    cursor = conn.cursor()

    # Reconfirma que a compra pertence ao usuário e ainda está sem plantio vinculado
    cursor.execute("""
        SELECT id, fornecedor_id FROM compras
        WHERE id = ? AND usuario_id = ? AND status = 'retirado' AND plantio_id IS NULL
    """, (int(compra_id), session["usuario_id"]))
    compra = cursor.fetchone()

    if not compra:
        conn.close()
        flash("Operação inválida. A compra não foi encontrada ou já possui plantio.", "erro")
        return redirect("/plantios/pendentes")

    fornecedor_id = compra[1]

    # Salva foto ao lado da cova (Etapa 3) — foto_plantio
    foto_plantio = None
    if "foto_plantio" in request.files:
        arq = request.files["foto_plantio"]
        if arq and arq.filename:
            ext = arq.filename.rsplit(".", 1)[-1].lower()
            if ext in EXTENSOES_PERMITIDAS:
                nome = secure_filename(f"plantio_{session['usuario_id']}_{compra_id}_cova.{ext}")
                arq.save(os.path.join(UPLOAD_FOLDER, nome))
                foto_plantio = nome

    # Salva foto com a planta na cova e regada (Etapa 4) — acompanhamento_1
    foto_1 = None
    if "foto_acomp1" in request.files:
        arq = request.files["foto_acomp1"]
        if arq and arq.filename:
            ext = arq.filename.rsplit(".", 1)[-1].lower()
            if ext in EXTENSOES_PERMITIDAS:
                nome = secure_filename(f"plantio_{session['usuario_id']}_{compra_id}_acomp1.{ext}")
                arq.save(os.path.join(UPLOAD_FOLDER, nome))
                foto_1 = nome

    from datetime import date
    data_hoje = date.today().isoformat()

    # Insere o registro definitivo do plantio em plantas_go
    cursor.execute("""
        INSERT INTO plantas_go (
            data_plantio, responsavel_id, especie, municipio, bairro,
            latitude, longitude,
            foto_plantio, foto_1, acompanhamento_1,
            fornecedor_id, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data_hoje, session["usuario_id"], especie, municipio, bairro,
        latitude, longitude,
        foto_plantio, foto_1, data_hoje if foto_1 else None,
        fornecedor_id, "em_analise"
    ))

    # Recupera o id do plantio recém-criado para vincular à compra
    cursor.execute("""
        SELECT id FROM plantas_go
        WHERE responsavel_id = ? ORDER BY id DESC LIMIT 1
    """, (session["usuario_id"],))
    plantio = cursor.fetchone()
    plantio_id = plantio[0] if plantio else None

    # Vincula o plantio à compra para indicar que o ciclo de plantio foi iniciado
    if plantio_id:
        cursor.execute(
            "UPDATE compras SET plantio_id = ? WHERE id = ?",
            (plantio_id, int(compra_id))
        )

    conn.commit()
    conn.close()

    flash("Plantio registrado com sucesso! Acompanhe o desenvolvimento da sua muda.", "sucesso")
    return redirect("/plantios/pendentes")


# ===================== ROTA LOGOUT ADMINISTRADOR =====================
# Encerra a sessão do administrador e redireciona para o login.
@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect("/admin/login")

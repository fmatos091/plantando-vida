import sqlite3
import secrets
from flask import render_template, request, redirect, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

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
    return render_template("dashboard.html", nome=session["usuario_nome"])


# ===================== ROTA DE LOGOUT =====================
# Limpa todos os dados da sessão e redireciona para /login.
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


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

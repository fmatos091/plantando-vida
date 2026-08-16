from db import get_db
import secrets
import random
import os
import re
# google-genai: SDK oficial do Gemini, usado para identificar espécies por foto
# (ver /api/identificar-especie). A Gemini é uma IA multimodal generalista, não um
# serviço botânico dedicado, mas foi a alternativa escolhida por ter cadastro
# imediato (sem lista de espera) e um nível gratuito generoso.
import json
from google import genai
from google.genai import types as genai_types
from google.genai import errors as genai_errors

# ===================== VERSÃO DOS TERMOS DE USO =====================
# Altere este valor sempre que os Termos ou a Política de Privacidade forem atualizados.
# Todos os usuários serão obrigados a reler e aceitar novamente no próximo login.
VERSAO_TERMOS = "v1.0"
import uuid
import smtplib
import qrcode
import resend
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from email.utils import formataddr, parseaddr
from flask import render_template, request, redirect, session, flash, jsonify, g, send_from_directory, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import cloudinary
import cloudinary.uploader

# ===================== CLOUDINARY: CONFIGURAÇÃO =====================
# Credenciais lidas do ambiente (.env local ou variáveis do Railway em produção).
# Variáveis necessárias: CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET
cloudinary.config(
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME"),
    api_key    = os.environ.get("CLOUDINARY_API_KEY"),
    api_secret = os.environ.get("CLOUDINARY_API_SECRET"),
    secure     = True,
)

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


# Pastas locais — mantidas para compatibilidade com arquivos legados e QR codes gerados
UPLOAD_FOLDER  = os.path.join(os.path.dirname(__file__), "static", "uploads")
QRCODE_FOLDER  = os.path.join(os.path.dirname(__file__), "static", "qrcodes")
PIX_FOLDER     = os.path.join(os.path.dirname(__file__), "static", "pix")
EXTENSOES_PERMITIDAS = {"jpg", "jpeg", "png"}


# ===================== HELPER: UPLOAD CLOUDINARY =====================
# Faz upload de um objeto de arquivo (werkzeug FileStorage) para o Cloudinary.
# Retorna a URL segura (https://res.cloudinary.com/...) do arquivo enviado.
# pasta: subpasta dentro do projeto no Cloudinary (ex: 'plantios', 'pix').
def upload_cloudinary(arquivo, pasta="plantando-vida"):
    resultado = cloudinary.uploader.upload(
        arquivo,
        folder        = pasta,
        resource_type = "image",
    )
    return resultado["secure_url"]

from app import app
from datetime import datetime, timezone, timedelta

# Fuso horário do Brasil (UTC-3) — usado para datas locais e exibição de timestamps
TZ_BRASIL = timezone(timedelta(hours=-3))

# Filtro Jinja2: converte qualquer valor de data/datetime para DD/MM/YYYY no fuso Brasil.
# Aceita strings ISO (YYYY-MM-DD, com ou sem hora e timezone) e objetos datetime/date.
@app.template_filter('data_br')
def data_br_filter(valor):
    if not valor:
        return ''
    s = str(valor).strip()
    try:
        # Timestamp com timezone (ex: "2026-05-28 02:07:30.639063+00:00")
        if '+' in s[10:] or s.endswith('Z'):
            dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
            return dt.astimezone(TZ_BRASIL).strftime('%d/%m/%Y')
        # Datetime sem timezone (ex: "2026-05-28 02:07:30")
        if ' ' in s or 'T' in s:
            dt = datetime.fromisoformat(s)
            return dt.strftime('%d/%m/%Y')
        # Data pura (ex: "2026-05-28")
        return datetime.strptime(s[:10], '%Y-%m-%d').strftime('%d/%m/%Y')
    except Exception:
        return s


# ===================== HELPER: ENVIAR EMAIL (Brevo SMTP) =====================
# Envia email HTML transacional via Brevo SMTP — 300 emails/dia gratuitos.
# Variáveis de ambiente necessárias:
#   BREVO_LOGIN   — login SMTP fornecido pelo Brevo (ex: a93994001@smtp-brevo.com)
#   BREVO_SMTP_KEY — chave SMTP gerada no painel Brevo
#   BREVO_FROM    — endereço "De" exibido (ex: "Plantando Vida <noreply@seudominio.com.br>")
# Retorna True se enviado com sucesso, False caso contrário.
def enviar_email(destinatario, assunto, corpo_html):
    import sys
    login     = os.environ.get("BREVO_LOGIN", "")
    smtp_key  = os.environ.get("BREVO_SMTP_KEY", "")
    remetente = os.environ.get("BREVO_FROM", "Plantando Vida <projetoplantandovida2026@gmail.com>")

    if not login or not smtp_key:
        print(f"[EMAIL] Credenciais Brevo ausentes: BREVO_LOGIN={bool(login)} BREVO_SMTP_KEY={bool(smtp_key)}", file=sys.stderr)
        return False

    try:
        import base64 as _b64
        msg = MIMEMultipart("alternative")
        # Codifica assunto como bloco único base64 para evitar quebra de linha
        # que causa "Header parsing error" em servidores SMTP mais estritos (ex: Brevo).
        msg["Subject"] = "=?utf-8?b?" + _b64.b64encode(assunto.encode("utf-8")).decode("ascii") + "?="
        msg["From"]    = remetente
        msg["To"]      = destinatario
        # Indica que é email transacional (não marketing)
        msg["X-Mailer"]   = "Plantando Vida Mailer"

        msg.attach(MIMEText(corpo_html, "html", "utf-8"))

        # Extrai apenas o endereço de e-mail do remetente para o envelope SMTP.
        # sendmail() precisa do e-mail puro (sem "Nome <...>") no MAIL FROM.
        _, envelope_from = parseaddr(remetente)
        if not envelope_from:
            envelope_from = remetente  # fallback se formato inesperado

        # Brevo SMTP: host smtp-relay.brevo.com, porta 587, STARTTLS obrigatório
        with smtplib.SMTP("smtp-relay.brevo.com", 587, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(login, smtp_key)
            smtp.sendmail(envelope_from, destinatario, msg.as_string())

        print(f"[EMAIL] Enviado via Brevo para {destinatario}", file=sys.stderr)
        return True
    except smtplib.SMTPAuthenticationError as e:
        print(f"[EMAIL] ERRO autenticacao Brevo — verifique BREVO_LOGIN e BREVO_SMTP_KEY: {e}", file=sys.stderr)
        return False
    except smtplib.SMTPRecipientsRefused as e:
        print(f"[EMAIL] ERRO destinatario recusado {destinatario}: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[EMAIL] ERRO Brevo para {destinatario}: {type(e).__name__}: {e}", file=sys.stderr)
        return False


# ===================== HELPER: ENVIAR TOKEN DE CADASTRO =====================
# Gera um código de 6 dígitos, salva na sessão e envia por email HTML profissional.
# Retorna o token gerado. session["cadastro_email_enviado"] indica se o envio teve sucesso.
def _enviar_token_cadastro(email_dest, nome):
    # Gera token numérico de 6 dígitos separado em 2 grupos de 3 para leitura fácil
    token = "".join(random.choices("0123456789", k=6))
    token_exibido = f"{token[:3]} {token[3:]}"  # ex: "482 917"

    # Armazena token sem espaço na sessão (validação compara sem espaço)
    session["cadastro_token"] = token

    # Template de email responsivo e profissional com identidade visual do projeto
    corpo_html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Verificacao de Cadastro — Plantando Vida</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;">

  <!-- Wrapper -->
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 0;">
    <tr>
      <td align="center">

        <!-- Card principal -->
        <table width="520" cellpadding="0" cellspacing="0"
               style="background:#ffffff;border-radius:16px;overflow:hidden;
                      box-shadow:0 4px 24px rgba(0,0,0,.08);max-width:520px;width:100%;">

          <!-- Header verde -->
          <tr>
            <td style="background:#166534;padding:32px 40px;text-align:center;">
              <p style="margin:0 0 4px;font-size:22px;font-weight:700;color:#ffffff;
                        letter-spacing:0.5px;">Plantando Vida</p>
              <p style="margin:0;font-size:13px;color:#bbf7d0;letter-spacing:1px;
                        text-transform:uppercase;">Sistema de Gestao de Plantios</p>
            </td>
          </tr>

          <!-- Corpo -->
          <tr>
            <td style="padding:36px 40px 28px;">
              <p style="margin:0 0 8px;font-size:22px;font-weight:700;color:#111827;">
                Ola, {nome}!
              </p>
              <p style="margin:0 0 28px;font-size:15px;color:#4b5563;line-height:1.6;">
                Recebemos sua solicitacao de cadastro. Use o codigo abaixo para
                confirmar seu email e ativar sua conta:
              </p>

              <!-- Bloco do token -->
              <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td style="background:#f0fdf4;border:2px solid #86efac;
                              border-radius:12px;padding:24px;text-align:center;">
                    <p style="margin:0 0 6px;font-size:12px;font-weight:600;
                               color:#166534;letter-spacing:2px;text-transform:uppercase;">
                      Codigo de Verificacao
                    </p>
                    <p style="margin:0;font-size:48px;font-weight:700;
                               letter-spacing:10px;color:#14532d;font-family:monospace;">
                      {token_exibido}
                    </p>
                  </td>
                </tr>
              </table>

              <p style="margin:24px 0 0;font-size:13px;color:#6b7280;line-height:1.6;">
                Digite este codigo na tela de verificacao para concluir seu cadastro.
                <br>Se voce nao solicitou este cadastro, ignore este email.
              </p>
            </td>
          </tr>

          <!-- Divisor -->
          <tr>
            <td style="padding:0 40px;">
              <hr style="border:none;border-top:1px solid #e5e7eb;margin:0;">
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding:20px 40px 28px;text-align:center;">
              <p style="margin:0;font-size:11px;color:#9ca3af;line-height:1.6;">
                Este e um email automatico — nao responda a esta mensagem.<br>
                &copy; 2026 Plantando Vida. Todos os direitos reservados.
              </p>
            </td>
          </tr>

        </table>
        <!-- /Card -->

      </td>
    </tr>
  </table>

</body>
</html>"""

    ok = enviar_email(email_dest, "Codigo de verificacao — Plantando Vida", corpo_html)
    # Salva resultado na sessão para que a rota exiba o feedback correto ao usuário
    session["cadastro_email_enviado"] = ok
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


# ===================== TEMPLATE: LEMBRETE DE REGA E REGISTRO FOTOGRÁFICO =====================
# Monta o HTML do email de lembrete periódico (rega + fotos de acompanhamento),
# reaproveitando a identidade visual usada em _enviar_token_cadastro (header verde,
# card branco arredondado). O texto de cada seção é fixo (copy aprovado do projeto).
def _template_lembrete_rega(nome):
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Sua arvore precisa de voce — Plantando Vida</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 0;">
    <tr>
      <td align="center">
        <table width="560" cellpadding="0" cellspacing="0"
               style="background:#ffffff;border-radius:16px;overflow:hidden;
                      box-shadow:0 4px 24px rgba(0,0,0,.08);max-width:560px;width:100%;">

          <!-- Header verde -->
          <tr>
            <td style="background:#166534;padding:32px 40px;text-align:center;">
              <p style="margin:0 0 4px;font-size:22px;font-weight:700;color:#ffffff;
                        letter-spacing:0.5px;">🌱 Plantando Vida</p>
              <p style="margin:0;font-size:13px;color:#bbf7d0;letter-spacing:1px;
                        text-transform:uppercase;">Sua arvore precisa de voce</p>
            </td>
          </tr>

          <!-- Saudação e agradecimento -->
          <tr>
            <td style="padding:36px 40px 8px;">
              <p style="margin:0 0 8px;font-size:22px;font-weight:700;color:#111827;">
                Ola, {nome}!
              </p>
              <p style="margin:0 0 20px;font-size:15px;color:#4b5563;line-height:1.6;">
                Primeiro, queremos te agradecer por fazer parte do Projeto Plantando Vida.
                Cada muda que voce plantou representa um passo concreto por um futuro
                melhor — para o meio ambiente e para a nossa comunidade.
              </p>
              <p style="margin:0 0 8px;font-size:15px;color:#4b5563;line-height:1.6;">
                E e justamente por isso que queremos te lembrar de dois cuidados
                essenciais para que sua arvore cresca forte e saudavel:
              </p>
            </td>
          </tr>

          <!-- Seção: rega -->
          <tr>
            <td style="padding:12px 40px;">
              <p style="margin:0 0 10px;font-size:17px;font-weight:700;color:#166534;">
                🌧️ Regue com frequencia
              </p>
              <p style="margin:0 0 14px;font-size:14px;color:#4b5563;line-height:1.6;">
                Nos primeiros meses apos o plantio, a muda ainda esta se adaptando ao
                solo e depende diretamente da sua atencao para sobreviver. A falta de
                agua nessa fase e a principal causa do nao desenvolvimento das plantas.
              </p>
              <p style="margin:0 0 6px;font-size:14px;font-weight:700;color:#111827;">
                Nossas recomendacoes:
              </p>
              <ul style="margin:0 0 14px;padding-left:20px;font-size:14px;color:#4b5563;line-height:1.7;">
                <li>Regue ao menos 3 vezes por semana, preferencialmente de manha cedo ou no final da tarde.</li>
                <li>Em dias muito quentes ou secos, aumente a frequencia — o solo nao deve ficar ressecado.</li>
                <li>Regue na base da planta, evitando molhar as folhas diretamente.</li>
                <li>Se possivel, cubra o solo ao redor da muda com palha ou folhas secas para reter a umidade.</li>
              </ul>
              <p style="margin:0;font-size:14px;color:#4b5563;line-height:1.6;font-style:italic;">
                Lembre-se: uma arvore bem cuidada hoje pode viver por decadas — e cada gota d'agua faz diferenca!
              </p>
            </td>
          </tr>

          <tr><td style="padding:8px 40px;"><hr style="border:none;border-top:1px solid #e5e7eb;margin:0;"></td></tr>

          <!-- Seção: registro fotográfico -->
          <tr>
            <td style="padding:12px 40px;">
              <p style="margin:0 0 10px;font-size:17px;font-weight:700;color:#166534;">
                📸 Registre o crescimento com fotos
              </p>
              <p style="margin:0 0 14px;font-size:14px;color:#4b5563;line-height:1.6;">
                O registro fotografico e uma parte fundamental do Projeto Plantando Vida.
                Alem de comprovar o desenvolvimento da sua muda, as fotos constroem uma
                memoria viva do impacto que todos nos estamos gerando juntos.
              </p>
              <p style="margin:0 0 6px;font-size:14px;font-weight:700;color:#111827;">
                Por que isso e tao importante?
              </p>
              <ul style="margin:0 0 14px;padding-left:20px;font-size:14px;color:#4b5563;line-height:1.7;">
                <li>Suas fotos alimentam o mapa de plantios do projeto, mostrando a comunidade e as entidades beneficiadas o alcance real das acoes.</li>
                <li>O acompanhamento fotografico ajuda a identificar eventuais problemas — como pragas ou falta de agua — antes que se tornem serios.</li>
                <li>E a sua historia com essa arvore. Daqui a alguns anos, voce vai querer ver como tudo comecou!</li>
              </ul>
              <p style="margin:0 0 6px;font-size:14px;font-weight:700;color:#111827;">
                Como registrar pelo app:
              </p>
              <ol style="margin:0 0 14px;padding-left:20px;font-size:14px;color:#4b5563;line-height:1.7;">
                <li>Abra o Projeto Plantando Vida.</li>
                <li>Acesse "Acompanhamentos do Plantio".</li>
                <li>Tire uma foto da sua muda e confirme o registro.</li>
              </ol>
              <p style="margin:0;font-size:14px;color:#4b5563;line-height:1.6;font-style:italic;">
                Faca isso ao menos uma vez por mes — e rapido e faz toda a diferenca para o projeto!
              </p>
            </td>
          </tr>

          <tr><td style="padding:8px 40px;"><hr style="border:none;border-top:1px solid #e5e7eb;margin:0;"></td></tr>

          <!-- Fechamento -->
          <tr>
            <td style="padding:20px 40px 36px;">
              <p style="margin:0 0 10px;font-size:16px;font-weight:700;color:#166534;">
                🌳 Juntos, plantamos mais do que arvores
              </p>
              <p style="margin:0 0 20px;font-size:14px;color:#4b5563;line-height:1.6;">
                Cada rega, cada foto registrada, cada cuidado que voce dedica a sua muda
                fortalece nao apenas uma planta — fortalece o compromisso de toda uma
                comunidade com o meio ambiente e com as entidades que dependem deste projeto.
              </p>
              <p style="margin:0 0 4px;font-size:14px;color:#111827;">Obrigado por cuidar. Obrigado por fazer parte.</p>
              <p style="margin:0 0 20px;font-size:14px;color:#111827;">Com carinho,<br>Equipe Projeto Plantando Vida</p>
              <p style="margin:0;font-size:13px;color:#166534;font-weight:600;">
                🌱 Cada muda conta. Cada crianca importa. Cada futuro comeca aqui.
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


# ===================== ROTA CRON: LEMBRETE DE REGA E REGISTRO FOTOGRÁFICO =====================
# Endpoint protegido por token, feito para ser chamado por um cron externo gratuito
# (ex: cron-job.org), fora do app — não há scheduler embutido no processo Flask/gunicorn.
# A cada chamada, envia o lembrete apenas a quem está "devendo" há 20+ dias (ou nunca
# recebeu) — chamadas repetidas no mesmo dia NÃO duplicam envios, pois cada envio
# atualiza lembrete_rega_enviado_em imediatamente após o sucesso.
# Processa em lotes pequenos (LEMBRETE_REGA_BATCH) para não estourar o timeout do
# gunicorn (120s, ver Procfile) nem o limite diário de envios do Brevo (300/dia no
# plano gratuito). Recomendado: configurar o cron externo para chamar este endpoint
# a cada poucas horas, assim o lote drena o backlog aos poucos.
# Autenticação: header "X-Cron-Token" ou query string "?token=", comparado (em tempo
# constante) com a variável de ambiente CRON_SECRET.
LEMBRETE_REGA_INTERVALO_DIAS = 20
LEMBRETE_REGA_BATCH = 50

@app.route("/admin/cron/lembrete-rega", methods=["GET", "POST"])
def cron_lembrete_rega():
    token_esperado  = os.environ.get("CRON_SECRET", "")
    token_recebido  = request.headers.get("X-Cron-Token", "") or request.args.get("token", "")

    if not token_esperado or not secrets.compare_digest(token_recebido, token_esperado):
        return jsonify({"erro": "não autorizado"}), 401

    conn   = get_db()
    cursor = conn.cursor()

    # Só recebem o lembrete usuários com pelo menos um plantio aprovado
    # (são eles que têm, de fato, uma árvore para regar e fotografar).
    cursor.execute("""
        SELECT DISTINCT u.id, u.nome, u.email, u.lembrete_rega_enviado_em
        FROM usuarios u
        JOIN plantas_go pg ON pg.responsavel_id = u.id
        WHERE pg.status = 'aprovado' AND u.email IS NOT NULL AND u.email != ''
    """)
    candidatos = cursor.fetchall()

    # Filtra quem está devendo lembrete: nunca recebeu, ou o último envio
    # já passou do intervalo de 20 dias.
    agora     = datetime.now(TZ_BRASIL)
    intervalo = timedelta(days=LEMBRETE_REGA_INTERVALO_DIAS)
    devidos   = []  # cada item: (id, nome, email, nunca_enviado)

    for uid, nome, email, ultimo_envio in candidatos:
        if not ultimo_envio:
            devidos.append((uid, nome, email, True))
            continue
        try:
            dt_ultimo = datetime.fromisoformat(str(ultimo_envio))
            if dt_ultimo.tzinfo is None:
                dt_ultimo = dt_ultimo.replace(tzinfo=TZ_BRASIL)
        except Exception:
            devidos.append((uid, nome, email, True))
            continue
        if agora - dt_ultimo >= intervalo:
            devidos.append((uid, nome, email, False))

    # Prioriza quem nunca recebeu lembrete, depois quem espera há mais tempo
    devidos.sort(key=lambda d: not d[3])
    lote = devidos[:LEMBRETE_REGA_BATCH]

    enviados = []
    falhas   = []
    for uid, nome, email, _ in lote:
        html = _template_lembrete_rega(nome)
        ok   = enviar_email(email, "🌱 Sua árvore precisa de você — Regue e registre o crescimento!", html)
        if ok:
            cursor.execute(
                "UPDATE usuarios SET lembrete_rega_enviado_em = ? WHERE id = ?",
                (agora.isoformat(), uid)
            )
            conn.commit()  # comita a cada envio para não reenviar em caso de falha adiante no lote
            enviados.append(email)
        else:
            falhas.append(email)

    conn.close()

    return jsonify({
        "elegiveis_no_total":     len(candidatos),
        "devidos_no_total":       len(devidos),
        "processados_neste_lote": len(lote),
        "enviados":               len(enviados),
        "falhas":                 len(falhas),
    }), 200


# ===================== ROTA CRON: INFORMATIVO DIÁRIO DO SUPER ADMIN =====================
# Mesmo modelo de cron_lembrete_rega: protegido por CRON_SECRET, chamado de fora
# por um cron externo gratuito (ex: cron-job.org) uma vez por dia — não há
# scheduler embutido no processo Flask/gunicorn.
# Só envia de fato se informativo_config.ativo = 1 e houver e-mails cadastrados
# (ver /super-admin/painel, seção "Parâmetros"). Chamadas repetidas no mesmo dia
# não duplicam o snapshot (upsert por data), mas RE-ENVIAM o e-mail — o cron
# externo deve ser configurado para rodar uma vez por dia.
INFORMATIVO_JANELA_DIAS = 5

def _template_informativo_diario(totais, delta, tem_baseline, novos_usuarios, dias):
    """Monta o HTML do informativo diário enviado ao Super Admin."""
    def linha_delta(rotulo, valor):
        sinal = "+" if valor >= 0 else ""
        cor   = "#166534" if valor >= 0 else "#b91c1c"
        return f"""
        <tr>
          <td style="padding:10px 0;border-bottom:1px solid #e5e7eb;color:#374151;font-size:14px;">{rotulo}</td>
          <td style="padding:10px 0;border-bottom:1px solid #e5e7eb;text-align:right;font-weight:700;color:{cor};font-size:14px;">{sinal}{valor}</td>
        </tr>"""

    linhas_delta = "".join([
        linha_delta("👤 Usuários Totais", delta["usuarios"]),
        linha_delta("🌳 Plantios Aprovados", delta["plantios"]),
        linha_delta("🏪 Fornecedores Ativos", delta["fornecedores"]),
        linha_delta("🌿 Plantios Voluntários", delta["voluntarios"]),
    ])

    if novos_usuarios:
        linhas_usuarios = "".join([
            f"""<tr>
                  <td style="padding:8px 0;border-bottom:1px solid #f3f4f6;color:#111827;font-size:13px;">{nome}</td>
                  <td style="padding:8px 0;border-bottom:1px solid #f3f4f6;color:#6b7280;font-size:13px;text-align:right;">{cidade or '—'}</td>
                </tr>"""
            for nome, cidade in novos_usuarios
        ])
        bloco_novos_usuarios = f"""
        <p style="margin:28px 0 10px;font-size:14px;font-weight:700;color:#111827;">
            🆕 Novos usuários no período
        </p>
        <table width="100%" cellpadding="0" cellspacing="0">
          <thead>
            <tr>
              <th style="text-align:left;font-size:11px;color:#9ca3af;text-transform:uppercase;padding-bottom:6px;">Nome</th>
              <th style="text-align:right;font-size:11px;color:#9ca3af;text-transform:uppercase;padding-bottom:6px;">Cidade</th>
            </tr>
          </thead>
          <tbody>{linhas_usuarios}</tbody>
        </table>"""
    else:
        bloco_novos_usuarios = """
        <p style="margin:28px 0 0;font-size:13px;color:#9ca3af;">
            Nenhum usuário novo no período.
        </p>"""

    periodo_texto = (
        f"nos últimos {dias} dias" if tem_baseline
        else "desde o início do acompanhamento (ainda não há histórico de 5 dias)"
    )

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Informativo Diário — Plantando Vida</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 0;">
    <tr>
      <td align="center">
        <table width="560" cellpadding="0" cellspacing="0"
               style="background:#ffffff;border-radius:16px;overflow:hidden;
                      box-shadow:0 4px 24px rgba(0,0,0,.08);max-width:560px;width:100%;">

          <tr>
            <td style="background:#166534;padding:32px 40px;text-align:center;">
              <p style="margin:0 0 4px;font-size:22px;font-weight:700;color:#ffffff;">Plantando Vida</p>
              <p style="margin:0;font-size:13px;color:#bbf7d0;letter-spacing:1px;text-transform:uppercase;">
                Informativo Diário — Super Admin
              </p>
            </td>
          </tr>

          <tr>
            <td style="padding:36px 40px 8px;">
              <p style="margin:0 0 4px;font-size:18px;font-weight:700;color:#111827;">
                Aumento {periodo_texto}
              </p>
              <table width="100%" cellpadding="0" cellspacing="0" style="margin-top:12px;">
                {linhas_delta}
              </table>

              {bloco_novos_usuarios}

              <p style="margin:32px 0 10px;font-size:14px;font-weight:700;color:#111827;">
                📊 Totais até o momento
              </p>
              <table width="100%" cellpadding="0" cellspacing="0"
                     style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:12px;">
                <tr>
                  <td style="padding:16px 20px;font-size:13px;color:#166534;">👤 Usuários Totais</td>
                  <td style="padding:16px 20px;font-size:13px;color:#14532d;font-weight:700;text-align:right;">{totais['usuarios']}</td>
                </tr>
                <tr>
                  <td style="padding:0 20px 16px;font-size:13px;color:#166534;">🌳 Plantios Aprovados</td>
                  <td style="padding:0 20px 16px;font-size:13px;color:#14532d;font-weight:700;text-align:right;">{totais['plantios']}</td>
                </tr>
                <tr>
                  <td style="padding:0 20px 16px;font-size:13px;color:#166534;">🏪 Fornecedores Ativos</td>
                  <td style="padding:0 20px 16px;font-size:13px;color:#14532d;font-weight:700;text-align:right;">{totais['fornecedores']}</td>
                </tr>
                <tr>
                  <td style="padding:0 20px 16px;font-size:13px;color:#166534;">🌿 Plantios Voluntários</td>
                  <td style="padding:0 20px 16px;font-size:13px;color:#14532d;font-weight:700;text-align:right;">{totais['voluntarios']}</td>
                </tr>
              </table>
            </td>
          </tr>

          <tr>
            <td style="padding:20px 40px 28px;text-align:center;">
              <p style="margin:0;font-size:11px;color:#9ca3af;line-height:1.6;">
                Este é um e-mail automático do painel Super Admin — para deixar de
                recebê-lo, remova seu endereço em /super-admin/painel &rarr; Parâmetros.
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


@app.route("/admin/cron/informativo-diario", methods=["GET", "POST"])
def cron_informativo_diario():
    token_esperado = os.environ.get("CRON_SECRET", "")
    token_recebido = request.headers.get("X-Cron-Token", "") or request.args.get("token", "")

    if not token_esperado or not secrets.compare_digest(token_recebido, token_esperado):
        return jsonify({"erro": "não autorizado"}), 401

    conn   = get_db()
    cursor = conn.cursor()

    cursor.execute("SELECT ativo FROM informativo_config WHERE id = 1")
    linha_config = cursor.fetchone()
    if not linha_config or not linha_config[0]:
        conn.close()
        return jsonify({"ativo": False, "enviado": False, "motivo": "informativo desativado"}), 200

    cursor.execute("SELECT email FROM informativo_emails")
    destinatarios = [row[0] for row in cursor.fetchall()]
    if not destinatarios:
        conn.close()
        return jsonify({"ativo": True, "enviado": False, "motivo": "nenhum e-mail cadastrado"}), 200

    agora    = datetime.now(TZ_BRASIL)
    hoje_str = agora.strftime("%Y-%m-%d")

    # Totais atuais (os mesmos 4 cards do painel)
    totais = _totais_dashboard(cursor)

    # Snapshot de referência: o mais recente registrado em ou antes do corte de
    # N dias atrás. Se não existir (primeiros dias usando a feature), não há
    # baseline — o e-mail deixa isso explícito em vez de fingir uma comparação.
    corte_str = (agora - timedelta(days=INFORMATIVO_JANELA_DIAS)).strftime("%Y-%m-%d")
    cursor.execute("""
        SELECT total_usuarios, total_plantios_aprovados, total_fornecedores_ativos, total_voluntarios
        FROM informativo_snapshots
        WHERE data <= ?
        ORDER BY data DESC LIMIT 1
    """, (corte_str,))
    linha_baseline = cursor.fetchone()
    tem_baseline   = linha_baseline is not None
    baseline = {
        "usuarios":     linha_baseline[0] if linha_baseline else 0,
        "plantios":     linha_baseline[1] if linha_baseline else 0,
        "fornecedores": linha_baseline[2] if linha_baseline else 0,
        "voluntarios":  linha_baseline[3] if linha_baseline else 0,
    }
    delta = {chave: totais[chave] - baseline[chave] for chave in totais}

    # Usuários criados na janela — só os que têm criado_em preenchido entram
    # (cadastros anteriores à criação desta coluna ficam de fora, corretamente).
    corte_dt = (agora - timedelta(days=INFORMATIVO_JANELA_DIAS)).isoformat()
    cursor.execute("""
        SELECT nome, cidade FROM usuarios
        WHERE criado_em IS NOT NULL AND criado_em >= ?
        ORDER BY criado_em DESC
    """, (corte_dt,))
    novos_usuarios = cursor.fetchall()

    html = _template_informativo_diario(totais, delta, tem_baseline, novos_usuarios, INFORMATIVO_JANELA_DIAS)

    enviados, falhas = [], []
    for email in destinatarios:
        if enviar_email(email, "📋 Informativo Diário — Plantando Vida", html):
            enviados.append(email)
        else:
            falhas.append(email)

    # Upsert do snapshot de hoje (não duplica se o cron rodar 2x no mesmo dia)
    cursor.execute("SELECT id FROM informativo_snapshots WHERE data = ?", (hoje_str,))
    if cursor.fetchone():
        cursor.execute("""
            UPDATE informativo_snapshots
            SET total_usuarios = ?, total_plantios_aprovados = ?,
                total_fornecedores_ativos = ?, total_voluntarios = ?
            WHERE data = ?
        """, (totais["usuarios"], totais["plantios"], totais["fornecedores"], totais["voluntarios"], hoje_str))
    else:
        cursor.execute("""
            INSERT INTO informativo_snapshots
                (data, total_usuarios, total_plantios_aprovados, total_fornecedores_ativos, total_voluntarios)
            VALUES (?, ?, ?, ?, ?)
        """, (hoje_str, totais["usuarios"], totais["plantios"], totais["fornecedores"], totais["voluntarios"]))
    conn.commit()
    conn.close()

    return jsonify({
        "ativo":          True,
        "enviado":        len(enviados) > 0,
        "destinatarios":  len(destinatarios),
        "enviados":       len(enviados),
        "falhas":         len(falhas),
        "tem_baseline":   tem_baseline,
        "novos_usuarios": len(novos_usuarios),
    }), 200


# ===================== API: BUSCAR CNPJ POR CIDADE =====================


# ===================== ROTA HOME =====================
# Página inicial do sistema com apresentação e botões de acesso.
@app.route('/')
def home():
    # Estatísticas reais do banco para os contadores da home:
    # árvores plantadas = total de plantios aprovados (todos os tenants),
    # cidades = quantidade de cidades distintas cadastradas pelos usuários.
    total_arvores = _total_arvores_aprovadas()

    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(DISTINCT cidade) FROM usuarios
        WHERE cidade IS NOT NULL AND TRIM(cidade) != ''
    """)
    total_cidades = cursor.fetchone()[0]
    conn.close()

    return render_template('index.html',
                            total_arvores=total_arvores,
                            total_cidades=total_cidades)


# ===================== SERVICE WORKER (PWA) =====================
# Servido na raiz (e não em /static/sw.js) porque o escopo padrão de um Service
# Worker é a pasta de onde ele é servido — em /static/ ele só controlaria
# /static/*, e precisamos que cubra o site inteiro (scope "/" do manifest.json).
@app.route('/sw.js')
def service_worker():
    return send_from_directory(app.static_folder, 'sw.js', mimetype='application/javascript')


# ===================== WIDGET OXIGÊNIO =====================
# Constantes científicas usadas nos cálculos de O2/CO2 por árvore.
# Fonte: média de absorção/produção de uma árvore adulta ao longo de um ano.
O2_POR_ANO_KG   = 260   # kg de O2 produzidos por árvore por ano
CO2_POR_ANO_KG  = 20    # kg de CO2 absorvidos por árvore por ano
O2_RESPIRACAO_H = 60    # gramas de O2 que uma pessoa consome por hora


def _total_arvores_aprovadas():
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM plantas_go WHERE status = 'aprovado'")
    total = cursor.fetchone()[0]
    conn.close()
    return total


@app.route("/api/oxigenio")
def api_oxigenio():
    """Retorna o total de árvores aprovadas e os cálculos de O2/CO2 em tempo real."""
    total_arvores = _total_arvores_aprovadas()

    o2_por_hora_g = (O2_POR_ANO_KG * 1000) / 8760  # ~29.68 g/hora/árvore

    now = datetime.now()

    # Fração da hora atual (0.0 a 1.0)
    fracao_hora = (now.minute * 60 + now.second) / 3600

    # O2 acumulado nesta hora (sobe segundo a segundo)
    o2_hora_g = round(o2_por_hora_g * total_arvores * fracao_hora)

    # O2 produzido hoje em kg (desde meia-noite)
    horas_hoje = now.hour + fracao_hora
    o2_hoje_kg = round((horas_hoje / 24) * (O2_POR_ANO_KG * total_arvores / 365), 2)

    # CO2 absorvido neste ano até agora
    dia_do_ano = now.timetuple().tm_yday
    co2_ano_kg = round((dia_do_ano / 365) * CO2_POR_ANO_KG * total_arvores, 1)

    # Pessoas abastecidas com O2 por hora / por dia
    pessoas_hora = round((o2_por_hora_g * total_arvores) / O2_RESPIRACAO_H)
    pessoas_dia  = round((o2_por_hora_g * total_arvores * 24) / (O2_RESPIRACAO_H * 24))

    return jsonify({
        "total_arvores"  : total_arvores,
        "o2_hora_g"      : o2_hora_g,
        "o2_hoje_kg"     : o2_hoje_kg,
        "co2_ano_kg"     : co2_ano_kg,
        "pessoas_hora"   : pessoas_hora,
        "pessoas_dia"    : pessoas_dia,
        "fracao_hora_pct": round(fracao_hora * 100, 1),
        "timestamp"      : now.strftime("%H:%M:%S"),
    })


@app.route("/oxigenio")
def oxigenio():
    """Página do Widget Oxigênio: detalhamento de O2/CO2 em tempo real."""
    return render_template("oxigenio.html", total_arvores=_total_arvores_aprovadas())


# ===================== DESCOBRIR ESPÉCIE (IDENTIFICAÇÃO POR FOTO) =====================
# Ferramenta de engajamento acessível na home: o usuário fotografa uma planta com a
# câmera do celular e recebe de volta o nome da espécie.
#
# Serviço escolhido — Gemini API (Google AI Studio):
#   - Originalmente a rota usava a Pl@ntNet (base botânica dedicada), mas o
#     cadastro em my.plantnet.org ficou instável e sem previsão de acesso.
#   - A Gemini é uma IA multimodal generalista (não um índice botânico), mas tem
#     cadastro imediato em https://aistudio.google.com/apikey (sem lista de espera)
#     e nível gratuito generoso — por isso exige internet, como a opção anterior.
#   - Trade-off aceito: sem uma base de imagens de referência, a resposta não traz
#     miniatura/crédito de autor — o front usa a própria foto do usuário como imagem.
#
# A chave fica só no servidor (variável GEMINI_API_KEY_ESPECIES): a foto vai do navegador
# para cá e somente o nosso backend conversa com a Gemini. Assim a chave nunca é
# exposta no HTML/JS.
#
# A rota sempre responde JSON no formato {ok: true, ...} ou {ok: false, erro: "..."},
# para o modal da home exibir a mensagem pronta ao usuário sem traduzir códigos.
GEMINI_MODEL           = "gemini-flash-latest"  # alias que a Google mantém no modelo flash vigente,
                                                 # evita quebrar de novo quando uma versão fixa (ex: 2.5-flash) for desativada
IDENTIFICAR_MAX_BYTES  = 8 * 1024 * 1024    # 8 MB — o front já reduz a imagem antes de enviar

# Cliente único, reaproveitado entre requisições — criado uma vez na subida do app,
# igual ao cloudinary.config() acima. None quando a chave não está configurada, o
# que a rota trata como "ferramenta ainda não ativada" em vez de estourar erro.
_GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY_ESPECIES", "").strip()
_cliente_gemini = genai.Client(api_key=_GEMINI_API_KEY) if _GEMINI_API_KEY else None

# Instrui a IA a se comportar como um identificador botânico e a responder em um
# JSON fixo — o mesmo formato que a rota espera para montar a resposta ao front.
_PROMPT_IDENTIFICACAO = (
    "Você é um botânico especialista em identificação de plantas e árvores por foto. "
    "Analise a imagem enviada e identifique a espécie. "
    "Responda SOMENTE com um JSON válido, sem markdown e sem texto fora do JSON, "
    "exatamente neste formato: "
    '{"reconhecida": true ou false, "nome_popular": "", "nome_cientifico": "", '
    '"familia": "", "confianca_pct": 0, "motivo_nao_reconhecida": ""}. '
    "Defina \"reconhecida\": false quando a imagem não mostrar claramente uma planta "
    "ou quando você não tiver confiança razoável na identificação — nesse caso, "
    "explique o motivo em português, de forma breve e amigável, no campo "
    "\"motivo_nao_reconhecida\" (ex.: foto borrada, ângulo insuficiente, não parece "
    "ser uma planta). Nomes populares e explicações sempre em português do Brasil. "
    "Se não souber um nome popular em português, deixe \"nome_popular\" como string vazia. "
    "\"confianca_pct\" é sua confiança de 0 a 100 na identificação."
)


def _extrair_json(texto):
    """Converte a resposta em texto da IA em dict, tolerando cercas ```json residuais."""
    texto = (texto or "").strip()
    texto = re.sub(r"^```(?:json)?|```$", "", texto, flags=re.MULTILINE).strip()
    try:
        return json.loads(texto)
    except ValueError:
        casamento = re.search(r"\{.*\}", texto, re.DOTALL)
        if not casamento:
            return None
        try:
            return json.loads(casamento.group(0))
        except ValueError:
            return None


@app.route("/api/identificar-especie", methods=["POST"])
def api_identificar_especie():
    """Recebe a foto tirada pelo usuário e devolve o nome da espécie identificada pela IA."""
    if not _cliente_gemini:
        return jsonify({
            "ok": False,
            "erro": "A identificação de espécies ainda não foi ativada neste servidor.",
        })

    foto = request.files.get("foto")
    if not foto or not foto.filename:
        return jsonify({"ok": False, "erro": "Nenhuma foto foi recebida. Tente novamente."})

    # Lê em memória para conferir o tamanho antes de repassar ao serviço externo
    conteudo = foto.read()
    if not conteudo:
        return jsonify({"ok": False, "erro": "A foto chegou vazia. Tire outra e tente novamente."})
    if len(conteudo) > IDENTIFICAR_MAX_BYTES:
        return jsonify({"ok": False, "erro": "A foto é muito grande. Tire outra um pouco mais distante."})

    try:
        resposta = _cliente_gemini.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                _PROMPT_IDENTIFICACAO,
                genai_types.Part.from_bytes(data=conteudo, mime_type=foto.mimetype or "image/jpeg"),
            ],
            config=genai_types.GenerateContentConfig(response_mime_type="application/json"),
        )
    except genai_errors.ClientError as erro:
        # 429 = cota diária do nível gratuito esgotada; demais erros de cliente
        # (ex: chave inválida, modelo indisponível) não devem expor detalhes
        # internos ao usuário final — mas ficam logados para diagnóstico no Railway.
        print(f"[ESPECIE] ClientError code={getattr(erro, 'code', None)}: {erro}", file=sys.stderr)
        if getattr(erro, "code", None) == 429:
            return jsonify({
                "ok": False,
                "erro": "O limite diário de identificações foi atingido. Tente novamente amanhã.",
            })
        return jsonify({"ok": False, "erro": "Não foi possível analisar esta foto. Tente novamente."})
    except genai_errors.APIError as erro:
        print(f"[ESPECIE] APIError code={getattr(erro, 'code', None)}: {erro}", file=sys.stderr)
        return jsonify({
            "ok": False,
            "erro": "O serviço de identificação está indisponível neste momento. Tente mais tarde.",
        })
    except Exception as erro:
        print(f"[ESPECIE] ERRO inesperado {type(erro).__name__}: {erro}", file=sys.stderr)
        return jsonify({
            "ok": False,
            "erro": "Não conseguimos falar com o serviço de identificação. Verifique sua internet.",
        })

    dados = _extrair_json(getattr(resposta, "text", ""))
    if dados is None:
        print(f"[ESPECIE] resposta sem JSON válido: {getattr(resposta, 'text', '')!r}", file=sys.stderr)
        return jsonify({"ok": False, "erro": "Resposta inesperada do serviço de identificação."})

    if not dados.get("reconhecida"):
        return jsonify({
            "ok": False,
            "erro": dados.get("motivo_nao_reconhecida") or
                    "Não reconhecemos nenhuma planta nessa foto. Tente uma imagem mais nítida e bem iluminada.",
        })

    try:
        confianca = round(float(dados.get("confianca_pct") or 0))
    except (TypeError, ValueError):
        confianca = 0

    return jsonify({
        "ok"             : True,
        "nome_popular"   : (dados.get("nome_popular") or "").strip(),
        "nome_cientifico": (dados.get("nome_cientifico") or "").strip(),
        "familia"        : (dados.get("familia") or "").strip(),
        "confianca_pct"  : max(0, min(100, confianca)),
    })


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
        email_ok = session.get("cadastro_email_enviado", False)

        if os.environ.get("FLASK_ENV") == "production":
            if email_ok:
                flash(f"Codigo enviado para {email}. Verifique sua caixa de entrada (e o spam).", "sucesso")
            else:
                # Falha no envio — informa o usuário para contatar o suporte
                flash("Nao foi possivel enviar o email de verificacao. Verifique o endereco ou tente novamente em instantes.", "erro")
        else:
            flash("Codigo gerado. Veja o codigo exibido abaixo.", "sucesso")

        return redirect("/cadastros?etapa=token")

    # ── Etapa 2: valida o token informado pelo usuário ──
    elif etapa == "token":
        # Remove espaços para aceitar tanto "482917" quanto "482 917" (formato do email)
        token_informado = re.sub(r"\s+", "", request.form.get("token", "").strip())
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

        # Grava o novo usuário com senha hasheada.
        # tenant_id = NULL: usuário global — sem vínculo fixo a nenhum tenant.
        # O vínculo ocorre apenas via membros / membros_admin cadastrados pelo admin do tenant.
        conn   = get_db()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """INSERT INTO usuarios
                   (nome, email, senha, cpf, telefone, data_nascimento, uf, cidade, tenant_id, criado_em)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pending["nome"], pending["email"],
                    generate_password_hash(senha),
                    pending["cpf"], pending["telefone"], pending["data_nascimento"],
                    pending.get("uf"), pending.get("cidade"),
                    None,  # global — não pertence a nenhum tenant por padrão
                    datetime.now(TZ_BRASIL).isoformat(),
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

            # Redireciona para aceite de termos a cada login (LGPD)
            return redirect("/termos")

        # Credenciais inválidas: mensagem genérica para não expor qual campo falhou
        flash("Email, CPF ou senha incorretos. Tente novamente.", "erro")
        return redirect("/login")

    return render_template("login.html")


# ===================== ROTA DO DASHBOARD =====================
# ===================== ROTA TERMOS DE USO / LGPD =====================
# GET:  pública — serve também como URL de Política de Privacidade exigida pela
#       Play Store e por links no rodapé do site, então não pode exigir login.
#       Para o usuário logado, a página mostra o checkbox + botão de aceite;
#       para visitante anônimo (Google review, links externos), mostra só o texto.
# POST: registra o aceite na tabela aceites_termos com versão, IP e timestamp,
#       marca a sessão como aceita e redireciona para o dashboard. Continua
#       exigindo login — só faz sentido aceitar estando autenticado.
# Fluxo do usuário logado: /login → /termos → /dashboard.
@app.route("/termos", methods=["GET", "POST"])
def termos():
    if request.method == "POST":
        if "usuario_id" not in session:
            return redirect("/login")

        # Registra o aceite com IP real (considera proxy reverso do Railway/Render)
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        conn   = get_db()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO aceites_termos (usuario_id, versao, ip) VALUES (?, ?, ?)",
            (session["usuario_id"], VERSAO_TERMOS, ip)
        )
        conn.commit()
        conn.close()

        # Marca a sessão atual como com termos aceitos
        session["termos_aceitos"] = True
        return redirect("/dashboard")

    # GET: página pública — logado=False esconde o bloco de aceite no template
    return render_template("termos.html", versao=VERSAO_TERMOS, logado=("usuario_id" in session))


# Exibe o painel do usuário após login.
# Se o usuário não estiver logado (sem sessão), redireciona para /login.
# Se os termos ainda não foram aceitos na sessão atual, redireciona para /termos.
@app.route("/dashboard")
def dashboard():
    if "usuario_id" not in session:
        return redirect("/login")
    if not session.get("termos_aceitos"):
        return redirect("/termos")

    conn   = get_db()
    cursor = conn.cursor()

    # Conta quantos plantios do usuário foram aprovados pelo admin.
    # Usado no dashboard para exibir o Card 2 apenas quando há plantas aprovadas.
    cursor.execute(
        "SELECT COUNT(*) FROM plantas_go WHERE responsavel_id = ? AND status = 'aprovado'",
        (session["usuario_id"],)
    )
    total_aprovados = cursor.fetchone()[0]

    # Conta fornecedores ativos do tenant — usado em conjunto com membros_admin.
    # Filtra por tenant_id para não exibir fornecedores de outros projetos.
    tid_pub = getattr(g, "tenant_id", None) or 1
    cursor.execute("SELECT COUNT(*) FROM fornecedores WHERE ativo = 1 AND tenant_id = ?", (tid_pub,))
    fornecedores_ativos = cursor.fetchone()[0]

    # Verifica se o CPF do usuário logado está cadastrado como membro da administração.
    # Somente membros cadastrados em "Cad. Administração" podem ver o botão
    # "Adquirir em Local Credenciado" no dashboard.
    cursor.execute("SELECT cpf FROM usuarios WHERE id = ?", (session["usuario_id"],))
    usuario_row  = cursor.fetchone()
    cpf_usuario  = re.sub(r"\D", "", usuario_row[0] or "") if usuario_row else ""

    e_membro_admin   = False
    e_membro_escolar = False
    if cpf_usuario:
        # Comparação robusta: ignora formatação do CPF armazenado
        cursor.execute("""
            SELECT id FROM membros_admin
            WHERE REPLACE(REPLACE(REPLACE(cpf, '.', ''), '-', ''), ' ', '') = ?
              AND tenant_id = ?
        """, (cpf_usuario, tid_pub))
        e_membro_admin = cursor.fetchone() is not None

        # Verifica vínculo com qualquer Entidade Educacional do tenant
        cursor.execute("""
            SELECT m.id FROM membros m
            WHERE REPLACE(REPLACE(REPLACE(m.cpf, '.', ''), '-', ''), ' ', '') = ?
              AND m.tenant_id = ?
        """, (cpf_usuario, tid_pub))
        e_membro_escolar = cursor.fetchone() is not None

    # Busca entidades educacionais ativas para exibir no card "Plantio Voluntário".
    # Se nenhuma ativa, o card fica desabilitado com aviso.
    # ee[0]=id  ee[1]=razao_social  ee[2]=cnpj  ee[3]=whatsapp
    cursor.execute("""
        SELECT id, razao_social, cnpj, whatsapp
        FROM entidades_educacionais
        WHERE ativa = 1
        ORDER BY razao_social
    """)
    entidades_edu_ativas = cursor.fetchall()

    conn.close()

    return render_template("dashboard.html",
                           nome=session["usuario_nome"],
                           total_aprovados=total_aprovados,
                           fornecedores_ativos=fornecedores_ativos,
                           e_membro_admin=e_membro_admin,
                           e_membro_escolar=e_membro_escolar,
                           entidades_edu_ativas=entidades_edu_ativas)


# ===================== ROTA DE LOGOUT =====================
# Limpa todos os dados da sessão e redireciona para /login.
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ===================== HELPER: SALVAR FOTO =====================
# Valida a extensão do arquivo enviado e faz upload para o Cloudinary.
# Retorna a URL segura (https://...) ou None se inválido/vazio.
def salvar_foto(campo_arquivo):
    arquivo = request.files.get(campo_arquivo)
    if not arquivo or arquivo.filename == "":
        return None
    ext = arquivo.filename.rsplit(".", 1)[-1].lower()
    if ext not in EXTENSOES_PERMITIDAS:
        return None
    return upload_cloudinary(arquivo, pasta="plantando-vida/plantios")


# ===================== ROTA DE DIAGNÓSTICO TEMPORÁRIA =====================
# Exibe estado real do banco para depuração do fluxo Plantio Escolar.
# REMOVER após resolver o problema de CPF/tenant.
@app.route("/debug/plantio-escolar")
def debug_plantio_escolar():
    # Só funciona se logado como usuário regular
    if "usuario_id" not in session:
        return jsonify({"erro": "nao autenticado"}), 401

    conn   = get_db()
    cursor = conn.cursor()

    # Estado do usuário logado
    cursor.execute(
        "SELECT id, nome, email, cpf, tenant_id FROM usuarios WHERE id = ?",
        (session["usuario_id"],)
    )
    u = cursor.fetchone()

    # Todos os tenants
    cursor.execute("SELECT id, nome, slug, ativo FROM tenants")
    tenants = cursor.fetchall()

    # Todos os membros
    cursor.execute("SELECT id, nome, cpf, entidade_educacional_id, tenant_id FROM membros")
    membros = cursor.fetchall()

    # Todas as entidades educacionais
    cursor.execute("SELECT id, razao_social, ativa, tenant_id FROM entidades_educacionais")
    entidades = cursor.fetchall()

    # Todos os usuários
    cursor.execute("SELECT id, nome, cpf, tenant_id FROM usuarios")
    todos_usuarios = cursor.fetchall()

    # Todas as espécies (inclui entidade_educacional_id para diagnóstico)
    cursor.execute("SELECT id, nome, tipo, valor, entidade_educacional_id, tenant_id FROM especies_plantas")
    todas_especies = cursor.fetchall()

    conn.close()

    # CPF formatado que será usado na comparação (u[3] = cpf, u[4] = tenant_id)
    _cpf_digits = re.sub(r"\D", "", u[3] or "") if u and u[3] else ""
    cpf_formatado = ""
    if len(_cpf_digits) == 11:
        cpf_formatado = f"{_cpf_digits[0:3]}.{_cpf_digits[3:6]}.{_cpf_digits[6:9]}-{_cpf_digits[9:11]}"

    return jsonify({
        "session": {
            "usuario_id": session.get("usuario_id"),
            "usuario_nome": session.get("usuario_nome"),
            "tenant_id_admin": session.get("tenant_id"),
        },
        "g_tenant_id": getattr(g, "tenant_id", None),
        "usuario_logado": {
            "id": u[0], "nome": u[1], "email": u[2],
            "cpf_raw": u[3], "tenant_id": u[4]
        } if u else None,
        "cpf_para_comparacao": cpf_formatado,
        "tenants": [{"id": t[0], "nome": t[1], "slug": t[2], "ativo": t[3]} for t in tenants],
        "membros": [{"id": m[0], "nome": m[1], "cpf": m[2], "entidade_id": m[3], "tenant_id": m[4]} for m in membros],
        "entidades_educacionais": [{"id": e[0], "razao_social": e[1], "ativa": e[2], "tenant_id": e[3]} for e in entidades],
        "todos_usuarios": [{"id": u2[0], "nome": u2[1], "cpf": u2[2], "tenant_id": u2[3]} for u2 in todos_usuarios],
        "todas_especies": [{"id": s[0], "nome": s[1], "tipo": s[2], "valor": s[3], "entidade_educacional_id": s[4], "tenant_id": s[5]} for s in todas_especies],
        "query_doacoes_seria": f"WHERE entidade_educacional_id = 1 AND tenant_id = {getattr(g, 'tenant_id', None) or 1} AND valor = 0",
    })


# ===================== ROTA PLANTIO EM LOCAL CREDENCIADO =====================
# GET:  busca todos os fornecedores e exibe o formulário completo de registro.
#       Acesso restrito a usuários logados (session["usuario_id"]).
# POST: salva os dados do plantio na tabela plantas_go (incluindo fotos)
#       e registra a transação na tabela plantios com status "pendente".
@app.route("/plantio/escolar")
def plantio_escolar():
    # Exige login; redireciona para /login se não autenticado.
    if "usuario_id" not in session:
        return redirect("/login")

    # Tenant público detectado em before_request
    tid = getattr(g, "tenant_id", None) or 1

    # Busca todas as entidades educacionais ativas do tenant para seleção.
    # A verificação de CPF acontece apenas ao selecionar uma entidade específica
    # (rota /plantio/escolar/<id>/doacoes), não aqui.
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, razao_social, cnpj, whatsapp
        FROM entidades_educacionais
        WHERE ativa = 1 AND tenant_id = ?
        ORDER BY razao_social
    """, (tid,))
    entidades = cursor.fetchall()
    conn.close()

    return render_template("plantio_escolar.html", entidades=entidades)


# ===================== ROTA DOAÇÕES DE PLANTAS — PLANTIO ESCOLAR =====================
# Exibe espécies com valor = 0,00 (doações) para a entidade educacional selecionada.
# Verifica se o CPF do usuário logado está vinculado como membro dessa entidade.
# Caso não esteja, exibe mensagem e redireciona para a seleção de entidades.
@app.route("/plantio/escolar/<int:entidade_id>/doacoes")
def plantio_escolar_doacoes(entidade_id):
    if "usuario_id" not in session:
        return redirect("/login")

    # Tenant público detectado em before_request
    tid = getattr(g, "tenant_id", None) or 1

    conn   = get_db()
    cursor = conn.cursor()

    # Valida se a entidade existe, está ativa e pertence ao tenant
    cursor.execute(
        "SELECT id, razao_social, cnpj, whatsapp FROM entidades_educacionais WHERE id = ? AND ativa = 1 AND tenant_id = ?",
        (entidade_id, tid)
    )
    entidade = cursor.fetchone()
    if not entidade:
        conn.close()
        flash("Entidade educacional não encontrada ou inativa.", "erro")
        return redirect("/plantio/escolar")

    # Normaliza o CPF do usuário para somente dígitos.
    # membros.cpf pode estar salvo com ou sem máscara — REPLACE() no SQL
    # normaliza o campo do banco antes de comparar, tornando a comparação
    # robusta independente do formato armazenado.
    cursor.execute("SELECT cpf FROM usuarios WHERE id = ?", (session["usuario_id"],))
    u = cursor.fetchone()
    cpf_usuario = re.sub(r"\D", "", u[0] or "") if u and u[0] else ""

    # REPLACE() remove pontos, traços e espaços de membros.cpf antes de comparar
    cursor.execute(
        """SELECT id FROM membros
           WHERE REPLACE(REPLACE(REPLACE(cpf, '.', ''), '-', ''), ' ', '') = ?
             AND entidade_educacional_id = ?
             AND tenant_id = ?""",
        (cpf_usuario, entidade_id, tid)
    )
    if not cursor.fetchone():
        conn.close()
        flash(
            "Seu CPF não está vinculado a Entidade Educacional, "
            "volte mais tarde. Obrigado!",
            "erro"
        )
        return redirect("/plantio/escolar")

    # Busca espécies vinculadas a esta entidade educacional específica com valor = 0 (doação)
    cursor.execute(
        """SELECT id, nome, tipo, valor FROM especies_plantas
           WHERE valor = 0
             AND entidade_educacional_id = ?
             AND tenant_id = ?
           ORDER BY tipo, nome""",
        (entidade_id, tid)
    )
    especies = cursor.fetchall()
    conn.close()

    # Grava o ID da entidade na sessão para que o carrinho saiba para onde voltar
    session["origem_escolar_id"] = entidade_id

    return render_template("plantio_escolar_doacoes.html",
                           entidade=entidade,
                           especies=especies)


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

        # Tenant do usuário detectado em before_request — isola o plantio ao projeto correto
        tid_pub = getattr(g, "tenant_id", None) or 1

        # Insere o registro completo na tabela plantas_go com tenant_id.
        # fornecedor_id e status='em_analise' são gravados para rastreamento e aprovação.
        cursor.execute("""
            INSERT INTO plantas_go (
                data_plantio, responsavel_id, especie, municipio, bairro,
                latitude, longitude,
                acompanhamento_1, foto_1,
                acompanhamento_2, foto_2,
                acompanhamento_3, foto_3,
                fornecedor_id, status, tenant_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data_plantio, session["usuario_id"], especie, municipio, bairro,
            latitude, longitude,
            acomp_1, foto_1,
            acomp_2, foto_2,
            acomp_3, foto_3,
            int(fornecedor_id), "em_analise", tid_pub
        ))

        # Registra a transação na tabela plantios com tenant_id
        cursor.execute(
            "INSERT INTO plantios (usuario_id, fornecedor_id, tipo, status, tenant_id) VALUES (?, ?, ?, ?, ?)",
            (session["usuario_id"], int(fornecedor_id), "credenciado", "pendente", tid_pub)
        )

        conn.commit()
        conn.close()

        flash("Plantio registrado com sucesso! Aguardando aprovação.", "sucesso")
        return redirect("/dashboard")

    # GET: busca fornecedores ativos do tenant para exibição no catálogo.
    # Filtra por tenant_id para exibir somente fornecedores vinculados a este projeto.
    conn   = get_db()
    cursor = conn.cursor()
    tid_pub = getattr(g, "tenant_id", None) or 1
    cursor.execute("""
        SELECT id, razao_social, cnpj, cidade, uf, tipo_planta, whatsapp, maps_link
        FROM fornecedores
        WHERE ativo = 1 AND tenant_id = ?
        ORDER BY uf, cidade
    """, (tid_pub,))
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

    conn   = get_db()
    cursor = conn.cursor()

    # Busca plantios do usuário exceto os APROVADOS (que ficam em /plantios/aprovados).
    # Exibe: em_analise, reprovado, retirado.
    # p[0]=id  p[1]=data_plantio  p[2]=especie  p[3]=municipio  p[4]=bairro
    # p[5]=status  p[6]=justificativa  p[7]=criado_em  p[8]=fornecedor_nome
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

    # Busca compras do usuário SEM plantio vinculado — compras com plantio_id
    # preenchido ("Plantio Registrado") saem daqui e o acompanhamento fica em /plantios/aprovados.
    # c[0]=id  c[1]=especie_nome  c[2]=tipo_planta  c[3]=valor
    # c[4]=status  c[5]=criado_em  c[6]=fornecedor_nome  c[7]=comprovante  c[8]=plantio_id
    cursor.execute("""
        SELECT c.id, c.especie_nome, c.tipo_planta, c.valor,
               c.status, c.criado_em,
               COALESCE(f.razao_social, 'Fornecedor não informado') AS fornecedor_nome,
               c.comprovante, c.plantio_id
        FROM compras c
        LEFT JOIN fornecedores f ON f.id = c.fornecedor_id
        WHERE c.usuario_id = ? AND c.plantio_id IS NULL
        ORDER BY c.criado_em DESC
    """, (session["usuario_id"],))
    compras = cursor.fetchall()

    conn.close()

    # acomps não é mais necessário em pendentes — aprovados ficam em /plantios/aprovados
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
                return upload_cloudinary(arquivo, pasta="plantando-vida/acompanhamentos")
        return campo_atual  # mantém URL/nome já gravado

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


# ===================== ROTA ADICIONAR ACOMPANHAMENTO (tabela ilimitada) =====================
# Registra um único acompanhamento na tabela acompanhamentos.
# Permite quantidade ilimitada por plantio, ao contrário dos 3 campos fixos de plantas_go.
@app.route("/plantio/<int:pid>/acompanhamento", methods=["POST"])
def acompanhamento_add(pid):
    if "usuario_id" not in session:
        return redirect("/login")

    data_acomp = request.form.get("data_acomp", "").strip() or None

    foto = None
    arq = request.files.get("foto")
    if arq and arq.filename:
        ext = arq.filename.rsplit(".", 1)[-1].lower()
        if ext in EXTENSOES_PERMITIDAS:
            foto = upload_cloudinary(arq, pasta="plantando-vida/acompanhamentos")

    conn   = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id FROM plantas_go WHERE id = ? AND responsavel_id = ? AND status = 'aprovado'",
        (pid, session["usuario_id"])
    )
    if not cursor.fetchone():
        conn.close()
        flash("Plantio não encontrado ou não aprovado.", "erro")
        return redirect("/plantios/pendentes")

    cursor.execute(
        "INSERT INTO acompanhamentos (plantio_id, data_acomp, foto) VALUES (?, ?, ?)",
        (pid, data_acomp, foto)
    )
    conn.commit()
    conn.close()

    flash("Acompanhamento registrado com sucesso!", "sucesso")
    return redirect("/plantios/pendentes")


# ===================== ROTA PLANTIOS APROVADOS =====================
# Exibe plantios aprovados com acompanhamentos e link para mapa pessoal.
# p[0]=id  p[1]=data_plantio  p[2]=especie  p[3]=municipio  p[4]=bairro
# p[5]=criado_em  p[6]=fornecedor_nome  p[7]=foto_1
# p[8]=acomp_1  p[9]=acomp_2  p[10]=acomp_3
# p[11]=foto_2  p[12]=foto_3  p[13]=latitude  p[14]=longitude
@app.route("/plantios/aprovados")
def plantios_aprovados():
    if "usuario_id" not in session:
        return redirect("/login")

    conn   = get_db()
    cursor = conn.cursor()

    # Busca plantios aprovados com todos os campos de acompanhamento e coordenadas
    cursor.execute("""
        SELECT pg.id, pg.data_plantio, pg.especie, pg.municipio, pg.bairro,
               pg.criado_em,
               COALESCE(f.razao_social, 'Fornecedor não informado') AS fornecedor_nome,
               pg.foto_1,
               pg.acompanhamento_1, pg.acompanhamento_2, pg.acompanhamento_3,
               pg.foto_2, pg.foto_3,
               pg.latitude, pg.longitude
        FROM plantas_go pg
        LEFT JOIN fornecedores f ON f.id = pg.fornecedor_id
        WHERE pg.responsavel_id = ? AND pg.status = 'aprovado'
        ORDER BY pg.criado_em DESC
    """, (session["usuario_id"],))
    plantios = cursor.fetchall()

    # Acompanhamentos ilimitados (tabela separada) agrupados por plantio_id
    acomps = {}
    if plantios:
        pids         = [p[0] for p in plantios]
        placeholders = ','.join(['?'] * len(pids))
        cursor.execute(
            f"SELECT plantio_id, id, data_acomp, foto FROM acompanhamentos "
            f"WHERE plantio_id IN ({placeholders}) ORDER BY plantio_id, id",
            pids
        )
        for row in cursor.fetchall():
            acomps.setdefault(row[0], []).append(row)

    conn.close()

    return render_template("plantios_aprovados.html", plantios=plantios, acomps=acomps)


# ===================== ROTA MEU MAPA (mapa pessoal do usuário) =====================
# Exibe mapa com todos os plantios aprovados do usuário logado que têm coordenadas.
@app.route("/plantios/meu-mapa")
def plantios_meu_mapa():
    if "usuario_id" not in session:
        return redirect("/login")

    import json

    conn   = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT pg.id, pg.latitude, pg.longitude, pg.especie,
               pg.municipio, pg.bairro, pg.data_plantio,
               pg.foto_1, pg.foto_plantio
        FROM plantas_go pg
        WHERE pg.responsavel_id = ?
          AND pg.status = 'aprovado'
          AND pg.latitude  IS NOT NULL
          AND pg.longitude IS NOT NULL
        ORDER BY pg.data_plantio DESC
    """, (session["usuario_id"],))
    rows = cursor.fetchall()
    conn.close()

    def _foto_url(val):
        if not val:
            return None
        return val if val.startswith("http") else f"/static/uploads/{val}"

    marcadores = []
    for r in rows:
        data_raw = str(r[6]) if r[6] else ""
        try:
            data_br = datetime.strptime(data_raw, "%Y-%m-%d").strftime("%d/%m/%y") if data_raw else ""
        except ValueError:
            data_br = data_raw

        fotos = [u for u in [_foto_url(r[7]), _foto_url(r[8])] if u]
        marcadores.append({
            "id":        r[0],
            "lat":       float(r[1]),
            "lng":       float(r[2]),
            "especie":   r[3] or "",
            "municipio": r[4] or "",
            "bairro":    r[5] or "",
            "data":      data_br,
            "fotos":     fotos,
        })

    return render_template(
        "meu_mapa.html",
        marcadores_json=json.dumps(marcadores, ensure_ascii=False),
        total=len(marcadores),
        nome=session.get("usuario_nome", ""),
    )


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

    # Garante isolamento: só altera usuários que são membros deste tenant
    tid = get_tid()

    # Rejeita a edição se o usuário não tiver vínculo de membro com este tenant
    if not _usuario_pertence_ao_tenant(cursor, usuario_id, tid):
        conn.close()
        flash("Operação não permitida: usuário não é membro deste projeto.", "erro")
        return redirect("/admin/painel?tipo=usuarios")

    # Verifica se o novo email já pertence a outro usuário no sistema
    cursor.execute(
        "SELECT id FROM usuarios WHERE email = ? AND id != ?",
        (email, usuario_id)
    )
    if cursor.fetchone():
        conn.close()
        flash("Este email já está em uso por outro cadastro.", "erro")
        return redirect("/admin/painel?tipo=usuarios")

    # Atualiza os dados do usuário — segurança garantida pelo check de membro acima
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

    tid    = get_tid()
    conn   = get_db()
    cursor = conn.cursor()

    # Rejeita a ação se o usuário não tiver vínculo de membro com este tenant
    if not _usuario_pertence_ao_tenant(cursor, usuario_id, tid):
        conn.close()
        flash("Operação não permitida: usuário não é membro deste projeto.", "erro")
        return redirect("/admin/painel?tipo=usuarios")

    # Senha vazia impossibilita o login via check_password_hash para qualquer input.
    cursor.execute(
        "UPDATE usuarios SET senha=? WHERE id=?",
        ("", usuario_id)
    )
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

    tid    = get_tid()
    conn   = get_db()
    cursor = conn.cursor()

    # Rejeita a exclusão se o usuário não tiver vínculo de membro com este tenant
    if not _usuario_pertence_ao_tenant(cursor, usuario_id, tid):
        conn.close()
        flash("Operação não permitida: usuário não é membro deste projeto.", "erro")
        return redirect("/admin/painel?tipo=usuarios")

    cursor.execute("DELETE FROM usuarios WHERE id=?", (usuario_id,))
    conn.commit()
    conn.close()

    flash(f"Usuário #{usuario_id} excluído com sucesso.", "sucesso")
    return redirect("/admin/painel?tipo=usuarios")


# ===================== ROTA ADMIN: MIGRAR USUÁRIOS DO TENANT PADRÃO =====================
# Move usuários com tenant_id=1 (padrão legado) para o tenant do admin logado.
# Necessário quando o sistema foi implantado sem tenant_id correto nos registros antigos.
# Operação idempotente: executar novamente não afeta usuários já no tenant correto.
@app.route("/admin/migrar-usuarios-padrao", methods=["POST"])
def admin_migrar_usuarios_padrao():
    if not session.get("admin"):
        return redirect("/admin/login")

    tid = get_tid()
    if not tid or tid == 1:
        flash("Operação não permitida para o tenant padrão.", "erro")
        return redirect("/admin/painel?tipo=usuarios")

    conn   = get_db()
    cursor = conn.cursor()

    # Conta usuários elegíveis (tenant_id=1 = registros antigos sem tenant definido)
    cursor.execute("SELECT COUNT(*) FROM usuarios WHERE tenant_id = 1")
    total = cursor.fetchone()[0]

    if total == 0:
        conn.close()
        flash("Nenhum usuário legado encontrado para migrar.", "info")
        return redirect("/admin/painel?tipo=usuarios")

    # Reatribui todos os usuários do tenant padrão (id=1) ao tenant atual
    cursor.execute("UPDATE usuarios SET tenant_id = ? WHERE tenant_id = 1", (tid,))
    conn.commit()
    conn.close()

    flash(f"{total} usuário(s) migrado(s) com sucesso para este projeto.", "sucesso")
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

    # Atualiza status para aprovado — tenant_id garante isolamento entre tenants
    cursor.execute(
        "UPDATE plantas_go SET status = 'aprovado', justificativa = NULL WHERE id = ? AND tenant_id = ?",
        (plantio_id, get_tid())
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

    # Atualiza status para reprovado — tenant_id garante isolamento entre tenants
    cursor.execute(
        "UPDATE plantas_go SET status = 'reprovado', justificativa = ? WHERE id = ? AND tenant_id = ?",
        (justificativa, plantio_id, get_tid())
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


# ===================== CREDENCIAIS DO SUPER ADMINISTRADOR =====================
# Nível máximo do sistema — acima dos tenants. Gerencia todos os projetos/clientes.
# Autenticado exclusivamente via variáveis de ambiente; NUNCA armazenado no banco.
# Rotas exclusivas: /super-admin/login  /super-admin/painel  /super-admin/logout
SUPER_ADMIN_LOGIN = os.environ.get("SUPER_ADMIN_LOGIN", "")
_sa_plain         = os.environ.get("SUPER_ADMIN_SENHA", "")
SUPER_ADMIN_HASH  = generate_password_hash(_sa_plain) if _sa_plain else ""
del _sa_plain  # Apaga a variável com texto puro da memória imediatamente


# ===================== HELPER: TENANT ID DA SESSÃO ADMIN =====================
# Retorna o tenant_id armazenado na sessão do admin logado.
# Todas as queries do painel admin usam este valor para isolar dados por projeto.
# Retorna None se o admin não tiver tenant_id válido (deve forçar re-login).
def get_tid():
    """Retorna tenant_id do admin logado (isolamento multi-tenant)."""
    return session.get("tenant_id")


def _usuario_pertence_ao_tenant(cursor, usuario_id, tid):
    """Verifica se o usuário tem CPF vinculado a membros ou membros_admin do tenant.
    Usado como guarda de segurança antes de ações admin (editar, limpar senha, excluir).
    Retorna True se o vínculo existe, False caso contrário."""
    cursor.execute("""
        SELECT 1 FROM usuarios u
        WHERE u.id = ? AND (
            EXISTS (
                SELECT 1 FROM membros m
                WHERE REPLACE(REPLACE(REPLACE(m.cpf,'.',''),'-',''),' ','')
                    = REPLACE(REPLACE(REPLACE(u.cpf,'.',''),'-',''),' ','')
                  AND m.tenant_id = ?
            ) OR EXISTS (
                SELECT 1 FROM membros_admin ma
                WHERE REPLACE(REPLACE(REPLACE(ma.cpf,'.',''),'-',''),' ','')
                    = REPLACE(REPLACE(REPLACE(u.cpf,'.',''),'-',''),' ','')
                  AND ma.tenant_id = ?
            )
        )
    """, (usuario_id, tid, tid))
    return cursor.fetchone() is not None


# ===================== BEFORE REQUEST: DETECTAR TENANT PÚBLICO =====================
# Identifica o tenant nas rotas públicas (usuário final) via subdomínio.
# Em desenvolvimento local, usa a variável TENANT_SLUG do .env (padrão: 'padrao').
# O tenant_id é armazenado em g.tenant_id para uso nas rotas de cada request.
@app.before_request
def detectar_tenant_publico():
    """Detecta tenant via subdomínio ou variável de ambiente e salva em g.tenant_id."""
    # Rotas do Super Admin e Admin não passam por detecção de tenant público
    if request.path.startswith("/super-admin") or request.path.startswith("/admin"):
        g.tenant_id = None
        return

    # Extrai o subdomínio do host (ex: "goiania.app.com" → "goiania")
    host = request.host.split(":")[0]
    partes = host.split(".")
    slug = partes[0] if len(partes) >= 3 else os.environ.get("TENANT_SLUG", "padrao")

    # Ignora subdomínios genéricos
    if slug in ("www", "app", "localhost"):
        slug = os.environ.get("TENANT_SLUG", "padrao")

    # Busca o tenant ativo pelo slug.
    # Se o slug do domínio não corresponder a nenhum tenant (ex: domínio principal
    # como "projetoplantandovida.com.br" → slug="projetoplantandovida" não cadastrado),
    # tenta o slug configurado em TENANT_SLUG no ambiente antes de cair no tenant_id=1.
    # Isso permite configurar qual tenant o domínio principal serve via variável de ambiente.
    try:
        conn   = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM tenants WHERE slug = ? AND ativo = 1", (slug,))
        t = cursor.fetchone()

        # Domínio não mapeou nenhum tenant → tenta o TENANT_SLUG do ambiente
        if not t:
            slug_env = os.environ.get("TENANT_SLUG", "padrao")
            if slug_env != slug:
                cursor.execute("SELECT id FROM tenants WHERE slug = ? AND ativo = 1", (slug_env,))
                t = cursor.fetchone()

        conn.close()
        g.tenant_id = t[0] if t else 1  # fallback final = tenant 1
    except Exception:
        g.tenant_id = 1  # fallback seguro — nunca lança 500 por falha de detecção


# ===================== ROTAS SUPER ADMIN =====================
# Nível máximo do sistema. O Super Admin:
#   • Cria, edita e inativa tenants (projetos/clientes).
#   • Visualiza estatísticas globais de todos os tenants.
#   • Não tem acesso direto ao painel de cada tenant (/admin/painel).
# Autenticação: session["super_admin"] = True (independente de session["admin"]).

@app.route("/super-admin/login", methods=["GET", "POST"])
def super_admin_login():
    """GET: exibe login. POST: autentica Super Admin via variáveis de ambiente."""
    # Redireciona se já autenticado como Super Admin
    if session.get("super_admin"):
        return redirect("/super-admin/painel")

    if request.method == "POST":
        login = request.form.get("login", "").strip()
        senha = request.form.get("senha", "")

        # Valida credenciais: login por igualdade, senha via hash werkzeug
        if (login == SUPER_ADMIN_LOGIN
                and SUPER_ADMIN_HASH
                and check_password_hash(SUPER_ADMIN_HASH, senha)):
            session["super_admin"] = True
            session["super_admin_login"] = login
            flash("Bem-vindo ao painel Super Admin.", "sucesso")
            return redirect("/super-admin/painel")

        flash("Login ou senha incorretos.", "erro")
        return redirect("/super-admin/login")

    return render_template("super_admin_login.html")


def _totais_dashboard(cursor):
    """Os mesmos 4 totais exibidos nos cards do painel Super Admin — reaproveitado
    também pelo cron do informativo diário (ver cron_informativo_diario)."""
    cursor.execute("SELECT COUNT(*) FROM usuarios")
    total_usuarios = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM plantas_go WHERE status = 'aprovado'")
    total_plantios = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM fornecedores WHERE ativo = 1")
    total_fornecedores = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM plantas_go WHERE tipo = 'voluntario'")
    total_voluntarios = cursor.fetchone()[0]

    return {
        "usuarios":     total_usuarios,
        "plantios":     total_plantios,
        "fornecedores": total_fornecedores,
        "voluntarios":  total_voluntarios,
    }


@app.route("/super-admin/painel")
def super_admin_painel():
    """Painel principal: lista todos os tenants com estatísticas globais."""
    if not session.get("super_admin"):
        flash("Acesso restrito ao Super Admin.", "erro")
        return redirect("/super-admin/login")

    conn   = get_db()
    cursor = conn.cursor()

    # Busca todos os tenants com contagem de usuários vinculados
    cursor.execute("""
        SELECT t.id, t.nome, t.slug, t.login, t.ativo, t.criado_em,
               COUNT(u.id) AS total_usuarios
        FROM tenants t
        LEFT JOIN usuarios u ON u.tenant_id = t.id
        GROUP BY t.id, t.nome, t.slug, t.login, t.ativo, t.criado_em
        ORDER BY t.ativo DESC, t.nome
    """)
    tenants = cursor.fetchall()

    # Estatísticas globais do sistema (todos os tenants somados)
    totais = _totais_dashboard(cursor)

    # Parâmetros do informativo diário: interruptor ativo/desativado + e-mails cadastrados
    cursor.execute("SELECT ativo FROM informativo_config WHERE id = 1")
    linha_config = cursor.fetchone()
    informativo_ativo = bool(linha_config[0]) if linha_config else False

    cursor.execute("SELECT id, email, criado_em FROM informativo_emails ORDER BY criado_em")
    informativo_emails = cursor.fetchall()

    conn.close()

    resposta = make_response(render_template("super_admin_painel.html",
                           tenants=tenants,
                           total_usuarios=totais["usuarios"],
                           total_plantios=totais["plantios"],
                           total_fornecedores=totais["fornecedores"],
                           total_voluntarios=totais["voluntarios"],
                           informativo_ativo=informativo_ativo,
                           informativo_emails=informativo_emails))
    # Evita que o navegador ou algum proxy/CDN na frente do Render sirva uma
    # versão em cache desta página logo após ativar/desativar o informativo ou
    # cadastrar/remover e-mail — o painel precisa refletir o estado real do banco
    # a cada carregamento, nunca uma cópia antiga.
    resposta.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resposta


# ===================== PARÂMETROS: INFORMATIVO DIÁRIO =====================
# Liga/desliga o envio e gerencia a lista de e-mails que recebem o resumo diário
# do dashboard (ver cron_informativo_diario mais abaixo, que faz o envio de fato).
# Estado sempre DEFINIDO explicitamente pelo checkbox marcado/desmarcado no submit
# (nunca invertido a partir do valor atual) — um duplo clique/duplo submit não
# muda o resultado, sempre cai no mesmo estado que o checkbox mostrava.
@app.route("/super-admin/parametros/informativo/toggle", methods=["POST"])
def super_admin_informativo_toggle():
    if not session.get("super_admin"):
        return redirect("/super-admin/login")

    # Checkbox HTML só manda o campo quando marcado ("on"); ausente = desmarcado
    novo_estado = 1 if request.form.get("ativo") == "on" else 0

    conn   = get_db()
    cursor = conn.cursor()
    # UPSERT em vez de UPDATE puro: se por algum motivo o registro id=1 ainda não
    # existir (ex: falha no seed de inicialização), o UPDATE afetaria 0 linhas e
    # o estado pareceria "não salvar" — o ON CONFLICT garante que sempre existe
    # e sempre reflete o valor enviado. Sintaxe idêntica em SQLite e PostgreSQL.
    cursor.execute("""
        INSERT INTO informativo_config (id, ativo) VALUES (1, ?)
        ON CONFLICT (id) DO UPDATE SET ativo = excluded.ativo
    """, (novo_estado,))
    conn.commit()

    import sys
    print(f"[INFORMATIVO] toggle -> form.ativo={request.form.get('ativo')!r} novo_estado={novo_estado} rowcount={cursor.rowcount}", file=sys.stderr)

    conn.close()

    flash(
        "Informativo diário ativado." if novo_estado else "Informativo diário desativado.",
        "sucesso"
    )
    return redirect("/super-admin/painel")


@app.route("/super-admin/parametros/informativo/emails/adicionar", methods=["POST"])
def super_admin_informativo_email_adicionar():
    if not session.get("super_admin"):
        return redirect("/super-admin/login")

    email = request.form.get("email", "").strip().lower()
    if not email or "@" not in email:
        flash("Informe um e-mail válido.", "erro")
        return redirect("/super-admin/painel")

    conn   = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO informativo_emails (email) VALUES (?)", (email,))
        conn.commit()
        flash(f"E-mail {email} cadastrado para receber o informativo.", "sucesso")
    except Exception:
        conn.rollback()
        flash("Este e-mail já está cadastrado.", "erro")
    finally:
        conn.close()

    return redirect("/super-admin/painel")


@app.route("/super-admin/parametros/informativo/emails/<int:email_id>/remover", methods=["POST"])
def super_admin_informativo_email_remover(email_id):
    if not session.get("super_admin"):
        return redirect("/super-admin/login")

    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM informativo_emails WHERE id = ?", (email_id,))
    conn.commit()
    conn.close()

    flash("E-mail removido da lista do informativo.", "sucesso")
    return redirect("/super-admin/painel")


# ===================== ROTA SUPER ADMIN: MAPA DE PLANTIOS VOLUNTÁRIOS =====================
# Exibe mapa global com todos os plantios do tipo 'voluntario' de todos os tenants.
# Sem filtro de tenant — o super admin vê dados globais do sistema.
@app.route("/super-admin/mapa-voluntarios")
def super_admin_mapa_voluntarios():
    if not session.get("super_admin"):
        flash("Acesso restrito ao Super Admin.", "erro")
        return redirect("/super-admin/login")

    import json

    conn   = get_db()
    cursor = conn.cursor()

    # Busca plantios voluntários com coordenadas de todos os tenants
    cursor.execute("""
        SELECT pg.id,
               pg.latitude,
               pg.longitude,
               pg.especie,
               pg.municipio,
               pg.bairro,
               pg.data_plantio,
               u.nome        AS nome_usuario,
               t.nome        AS tenant_nome,
               pg.foto_plantio,
               pg.foto_1,
               pg.tipo_planta
        FROM plantas_go pg
        JOIN  usuarios u ON u.id  = pg.responsavel_id
        LEFT JOIN tenants t ON t.id = pg.tenant_id
        WHERE pg.tipo = 'voluntario'
          AND pg.latitude  IS NOT NULL
          AND pg.longitude IS NOT NULL
        ORDER BY pg.data_plantio DESC
    """)
    rows = cursor.fetchall()
    conn.close()

    def _foto_url(val):
        if not val:
            return None
        return val if val.startswith("http") else f"/static/uploads/{val}"

    marcadores = []
    for r in rows:
        # Valida e converte coordenadas — pula o registro se inválidas para não travar o mapa.
        # Protege contra strings malformadas, vazias ou valores fora do range geográfico.
        try:
            lat = float(r[1])
            lng = float(r[2])
        except (ValueError, TypeError):
            continue
        if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
            continue

        # Converte data para padrão BR DD/MM/AA
        data_raw = str(r[6]) if r[6] else ""
        try:
            data_br = datetime.strptime(data_raw, "%Y-%m-%d").strftime("%d/%m/%y") if data_raw else ""
        except ValueError:
            data_br = data_raw

        fotos = [u for u in [_foto_url(r[9]), _foto_url(r[10])] if u]
        marcadores.append({
            "id":         r[0],
            "lat":        lat,
            "lng":        lng,
            "especie":    r[3] or "",
            "municipio":  r[4] or "",
            "bairro":     r[5] or "",
            "data":       data_br,
            "nome":       r[7] or "",
            "tenant":     r[8] or "—",
            "fotos":      fotos,
            "tipo_planta": r[11] or "",   # Nativa ou Frutífera
        })

    return render_template(
        "super_admin_mapa_voluntarios.html",
        marcadores_json=json.dumps(marcadores, ensure_ascii=False),
        total=len(marcadores),
    )


@app.route("/super-admin/tenant/criar", methods=["POST"])
def super_admin_criar_tenant():
    """Cria um novo tenant com login e senha hasheada."""
    if not session.get("super_admin"):
        return redirect("/super-admin/login")

    nome  = request.form.get("nome", "").strip()
    slug  = request.form.get("slug", "").strip().lower().replace(" ", "-")
    login = request.form.get("login", "").strip()
    senha = request.form.get("senha", "")

    # Validações obrigatórias
    if not nome or not slug or not login or not senha:
        flash("Todos os campos são obrigatórios.", "erro")
        return redirect("/super-admin/painel")

    if len(senha) < 6:
        flash("A senha deve ter no mínimo 6 caracteres.", "erro")
        return redirect("/super-admin/painel")

    conn   = get_db()
    cursor = conn.cursor()
    try:
        # Grava o hash da senha — nunca o texto puro
        cursor.execute(
            "INSERT INTO tenants (nome, slug, login, senha_hash, ativo) VALUES (?, ?, ?, ?, 1)",
            (nome, slug, login, generate_password_hash(senha))
        )
        conn.commit()
        flash(f"Tenant '{nome}' criado com sucesso! Login: {login}", "sucesso")
    except Exception:
        # Violação de UNIQUE em slug ou login
        flash("Slug ou login já em uso. Escolha valores únicos.", "erro")
    finally:
        conn.close()

    return redirect("/super-admin/painel")


@app.route("/super-admin/tenant/<int:tid>/editar", methods=["POST"])
def super_admin_editar_tenant(tid):
    """Atualiza nome, slug e login do tenant. Senha só é alterada se informada."""
    if not session.get("super_admin"):
        return redirect("/super-admin/login")

    nome  = request.form.get("nome", "").strip()
    slug  = request.form.get("slug", "").strip().lower().replace(" ", "-")
    login = request.form.get("login", "").strip()
    senha = request.form.get("senha", "").strip()

    conn   = get_db()
    cursor = conn.cursor()
    try:
        if senha:
            # Atualiza incluindo nova senha hasheada
            cursor.execute(
                "UPDATE tenants SET nome=?, slug=?, login=?, senha_hash=? WHERE id=?",
                (nome, slug, login, generate_password_hash(senha), tid)
            )
        else:
            # Mantém a senha existente — só atualiza metadados
            cursor.execute(
                "UPDATE tenants SET nome=?, slug=?, login=? WHERE id=?",
                (nome, slug, login, tid)
            )
        conn.commit()
        flash("Tenant atualizado com sucesso.", "sucesso")
    except Exception:
        flash("Slug ou login já em uso por outro tenant.", "erro")
    finally:
        conn.close()

    return redirect("/super-admin/painel")


@app.route("/super-admin/tenant/<int:tid>/toggle", methods=["POST"])
def super_admin_toggle_tenant(tid):
    """Ativa ou inativa um tenant (alterna o campo ativo entre 0 e 1)."""
    if not session.get("super_admin"):
        return redirect("/super-admin/login")

    conn   = get_db()
    cursor = conn.cursor()

    # Lê status atual e inverte: 1 → inativa, 0 → reativa
    cursor.execute("SELECT ativo, nome FROM tenants WHERE id = ?", (tid,))
    row = cursor.fetchone()
    if row:
        novo = 0 if row[0] == 1 else 1
        cursor.execute("UPDATE tenants SET ativo = ? WHERE id = ?", (novo, tid))
        conn.commit()
        acao = "inativado" if novo == 0 else "reativado"
        flash(f"Tenant '{row[1]}' {acao} com sucesso.", "sucesso")
    conn.close()

    return redirect("/super-admin/painel")


@app.route("/super-admin/logout")
def super_admin_logout():
    """Encerra a sessão do Super Admin e redireciona para o login."""
    session.pop("super_admin", None)
    session.pop("super_admin_login", None)
    flash("Sessão Super Admin encerrada.", "sucesso")
    return redirect("/super-admin/login")


# ===================== ROTA LOGIN ADMINISTRADOR (MULTI-TENANT) =====================
# GET:  exibe o formulário de login do administrador.
# POST: autentica o admin consultando a tabela tenants (multi-tenant).
#       Cada tenant tem login e senha_hash próprios cadastrados pelo Super Admin.
#       Se corretos: session["admin"] = True + session["tenant_id"] = id do tenant.
#       session["tenant_id"] é a chave do isolamento — todas as queries admin a usam.
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    # Redireciona se já autenticado como admin de tenant
    if session.get("admin") and session.get("tenant_id"):
        return redirect("/admin/painel")

    if request.method == "POST":
        login = request.form.get("login", "").strip()
        senha = request.form.get("senha", "")

        conn   = get_db()
        cursor = conn.cursor()

        # Busca o tenant ativo pelo login informado
        cursor.execute(
            "SELECT id, nome, senha_hash FROM tenants WHERE login = ? AND ativo = 1",
            (login,)
        )
        tenant = cursor.fetchone()
        conn.close()

        # Valida: tenant existe, está ativo, e senha confere com o hash armazenado
        if tenant and check_password_hash(tenant[2], senha):
            session["admin"]       = True
            session["tenant_id"]   = tenant[0]   # 🔑 chave do isolamento multi-tenant
            session["tenant_nome"] = tenant[1]
            return redirect("/admin/painel")

        # Credenciais incorretas — mensagem genérica por segurança
        flash("Login ou senha incorretos.", "erro")
        return redirect("/admin/login")

    return render_template("admin_login.html")


# ===================== ROTA PAINEL ADMINISTRADOR =====================
# Exibe todos os fornecedores e cadastros do tenant logado, com filtro de busca.
# O filtro é aplicado via query string (?busca=termo&tipo=fornecedores|usuarios).
# Acesso bloqueado se session["admin"] ou session["tenant_id"] não estiverem ativos.
@app.route("/admin/painel")
def admin_painel():
    if not session.get("admin"):
        flash("Acesso restrito. Faça o login.", "erro")
        return redirect("/admin/login")

    # Garante que o tenant_id está na sessão (isolamento multi-tenant)
    tid = get_tid()
    if not tid:
        flash("Sessão inválida. Faça login novamente.", "erro")
        return redirect("/admin/login")

    conn   = get_db()
    cursor = conn.cursor()

    # Parâmetros de filtro vindos da URL (query string)
    busca           = request.args.get("busca", "").strip()
    tipo            = request.args.get("tipo", "")  # sem aba padrão — exibe tela inicial
    plantio_status  = request.args.get("plantio_status", "").strip()  # filtro de status na aba plantios

    # ---- Consulta Fornecedores do tenant — com filtro por razão social, CNPJ ou cidade ----
    # Inclui ativo e maps_link para permitir edição e exibir status no painel
    if busca:
        cursor.execute("""
            SELECT id, razao_social, cnpj, whatsapp, tipo_planta, cidade, uf, ativo, maps_link, email
            FROM fornecedores
            WHERE tenant_id = ?
              AND (razao_social LIKE ? OR cnpj LIKE ? OR cidade LIKE ?)
            ORDER BY ativo DESC, razao_social
        """, (tid, f"%{busca}%", f"%{busca}%", f"%{busca}%"))
    else:
        cursor.execute("""
            SELECT id, razao_social, cnpj, whatsapp, tipo_planta, cidade, uf, ativo, maps_link, email
            FROM fornecedores
            WHERE tenant_id = ?
            ORDER BY ativo DESC, razao_social
        """, (tid,))
    fornecedores = cursor.fetchall()

    # ---- Consulta Usuários do tenant — apenas os vinculados via membros ou membros_admin ----
    # Regra: um usuário só aparece no painel admin se seu CPF estiver cadastrado em
    # "Membros das Entidades Educacionais" OR "Membros da Administração" deste tenant.
    # u[0]=id  u[1]=nome  u[2]=email  u[3]=cpf  u[4]=telefone  u[5]=data_nascimento
    _cpf_norm = "REPLACE(REPLACE(REPLACE({}.cpf,'.',''),'-',''),' ','')"
    _vinculo_sql = f"""
        EXISTS (
            SELECT 1 FROM membros m
            WHERE {_cpf_norm.format('m')} = {_cpf_norm.format('u')}
              AND m.tenant_id = ?
        ) OR EXISTS (
            SELECT 1 FROM membros_admin ma
            WHERE {_cpf_norm.format('ma')} = {_cpf_norm.format('u')}
              AND ma.tenant_id = ?
        )
    """
    if busca:
        cursor.execute(f"""
            SELECT u.id, u.nome, u.email, u.cpf, u.telefone, u.data_nascimento
            FROM usuarios u
            WHERE ({_vinculo_sql})
              AND (u.nome LIKE ? OR u.email LIKE ? OR u.cpf LIKE ?)
        """, (tid, tid, f"%{busca}%", f"%{busca}%", f"%{busca}%"))
    else:
        cursor.execute(f"""
            SELECT u.id, u.nome, u.email, u.cpf, u.telefone, u.data_nascimento
            FROM usuarios u
            WHERE ({_vinculo_sql})
            ORDER BY u.nome
        """, (tid, tid))
    usuarios = cursor.fetchall()

    # ---- Consulta Plantios do tenant — dados do usuário, fornecedor, fotos e localização ----
    # p[0]=id(plantas_go)  p[1]=data_plantio  p[2]=especie  p[3]=municipio  p[4]=status
    # p[5]=justificativa   p[6]=criado_em     p[7]=nome_usuario  p[8]=fornecedor_nome
    # p[9]=bairro  p[10]=latitude  p[11]=longitude  p[12]=foto_plantio  p[13]=foto_1
    # p[14]=compra_id  (ID original da compra — mesmo número exibido ao usuário)
    cursor.execute("""
        SELECT pg.id, pg.data_plantio, pg.especie, pg.municipio,
               pg.status, pg.justificativa, pg.criado_em,
               u.nome, COALESCE(f.razao_social, 'Não informado') AS fornecedor_nome,
               pg.bairro, pg.latitude, pg.longitude,
               pg.foto_plantio, pg.foto_1,
               c.id AS compra_id
        FROM plantas_go pg
        JOIN usuarios u ON u.id = pg.responsavel_id
        LEFT JOIN fornecedores f ON f.id = pg.fornecedor_id
        LEFT JOIN compras c ON c.plantio_id = pg.id
        WHERE pg.tenant_id = ?
          AND (pg.tipo IS NULL OR pg.tipo != 'voluntario')
        ORDER BY pg.criado_em DESC
    """, (tid,))
    plantios = cursor.fetchall()

    # ---- Consulta Compras de Mudas do tenant (para o admin gerenciar) ----
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
        WHERE c.tenant_id = ?
          AND (c.tipo_planta IS NULL OR c.tipo_planta != 'Voluntário')
        ORDER BY c.criado_em DESC
    """, (tid,))
    compras = cursor.fetchall()

    # Contagem unificada por status: soma plantas_go + compras de cada status.
    # Exibida nos botões de filtro da aba plantios para dar visibilidade total ao admin.
    contagem_plantios = {
        'em_analise': sum(1 for p in plantios if p[4] == 'em_analise') + sum(1 for c in compras if c[5] == 'em_analise'),
        'aprovado':   sum(1 for p in plantios if p[4] == 'aprovado')   + sum(1 for c in compras if c[5] == 'aprovado'),
        'reprovado':  sum(1 for p in plantios if p[4] == 'reprovado')  + sum(1 for c in compras if c[5] == 'reprovado'),
        'retirado':   sum(1 for p in plantios if p[4] == 'retirado')   + sum(1 for c in compras if c[5] == 'retirado'),
    }

    # ---- Consulta Espécies de Plantas do tenant ----
    # e[4] = entidade_educacional_id (pode ser NULL para espécies pagas)
    cursor.execute(
        "SELECT id, nome, tipo, valor, entidade_educacional_id FROM especies_plantas WHERE tenant_id = ? ORDER BY nome",
        (tid,)
    )
    especies = cursor.fetchall()

    # ---- Entidades Educacionais ativas do tenant (para dropdown no form de espécies) ----
    cursor.execute(
        "SELECT id, razao_social FROM entidades_educacionais WHERE ativa = 1 AND tenant_id = ? ORDER BY razao_social",
        (tid,)
    )
    entidades_edu_admin = cursor.fetchall()

    # ---- Consulta Dados Bancários do tenant (registro único por tenant) ----
    cursor.execute(
        "SELECT nome_empresarial, banco, conta, agencia, chave_pix, qrcode_pix FROM dados_bancarios WHERE tenant_id = ? LIMIT 1",
        (tid,)
    )
    dados_bancarios = cursor.fetchone()

    # ---- Consulta Entidades Favorecidas do tenant (lista unificada com dados bancários) ----
    # e[0]=id  e[1]=razao_social  e[2]=cnpj  e[3]=whatsapp  e[4]=criado_em
    # e[5]=banco  e[6]=conta  e[7]=agencia  e[8]=chave_pix  e[9]=qrcode_pix  e[10]=ativa
    cursor.execute("""
        SELECT id, razao_social, cnpj, whatsapp, criado_em,
               banco, conta, agencia, chave_pix, qrcode_pix, ativa
        FROM entidades
        WHERE tenant_id = ?
        ORDER BY ativa DESC, razao_social
    """, (tid,))
    entidades = cursor.fetchall()

    # Lista de entidades com ativa=1 (usada no template de Saldos Pendentes para seleção)
    entidades_ativas = [e for e in entidades if e[10] == 1]

    # ---- Consulta Entidades Educacionais do tenant (escolas, ONGs, creches, instituições) ----
    # Tabela separada das entidades financeiras — sem vínculo com faturamento.
    # ee[0]=id  ee[1]=razao_social  ee[2]=cnpj  ee[3]=whatsapp
    # ee[4]=banco  ee[5]=agencia  ee[6]=conta  ee[7]=chave_pix  ee[8]=qrcode_pix
    # ee[9]=ativa  ee[10]=criado_em
    cursor.execute("""
        SELECT id, razao_social, cnpj, whatsapp,
               banco, agencia, conta, chave_pix, qrcode_pix,
               ativa, criado_em
        FROM entidades_educacionais
        WHERE tenant_id = ?
        ORDER BY ativa DESC, razao_social
    """, (tid,))
    entidades_edu = cursor.fetchall()

    # ---- Consulta Membros vinculados às Entidades Educacionais do tenant ----
    # m[0]=id  m[1]=nome  m[2]=cpf  m[3]=entidade_educacional_id  m[4]=razao_social
    # Usados no painel para listar, cadastrar e excluir membros por entidade.
    cursor.execute("""
        SELECT m.id, m.nome, m.cpf, m.entidade_educacional_id,
               ee.razao_social
        FROM membros m
        JOIN entidades_educacionais ee ON ee.id = m.entidade_educacional_id
        WHERE m.tenant_id = ?
        ORDER BY ee.razao_social, m.nome
    """, (tid,))
    membros = cursor.fetchall()

    # ---- Consulta Membros da Administração do tenant ----
    # ma[0]=id  ma[1]=nome  ma[2]=cpf  ma[3]=criado_em
    # CPFs autorizados a exibir o botão "Adquirir em Local Credenciado" no dashboard.
    cursor.execute("""
        SELECT id, nome, cpf, criado_em
        FROM membros_admin
        WHERE tenant_id = ?
        ORDER BY nome
    """, (tid,))
    membros_admin = cursor.fetchall()

    # ---- Consulta Percentuais de Vigência do tenant ----
    # p[0]=id  p[1]=inicio_vigencia  p[2]=perc_fornecedor  p[3]=perc_entidade  p[4]=perc_admin
    cursor.execute("""
        SELECT id, inicio_vigencia, perc_fornecedor, perc_entidade, perc_admin, criado_em
        FROM percentuais_vigencia
        WHERE tenant_id = ?
        ORDER BY inicio_vigencia DESC
    """, (tid,))
    percentuais = cursor.fetchall()

    # ---- Cálculo dos Fechamentos Mensais Pendentes (Fornecedor, Entidade e Admin) ----
    # Inclui os 3 percentuais para que o helper genérico possa usar o correto por tipo.
    cursor.execute("""
        SELECT inicio_vigencia, perc_fornecedor, perc_entidade, perc_admin
        FROM percentuais_vigencia
        WHERE tenant_id = ?
        ORDER BY inicio_vigencia ASC
    """, (tid,))
    vigencias_asc = cursor.fetchall()

    fechamento_pendente          = _calcular_fechamento_pendente(cursor, vigencias_asc, 'fornecedor', tid)
    # Entidade e Admin usam função própria: totaliza por mês para exibir 1 card por período.
    fechamento_entidade_pendente = _calcular_fechamento_entidade_por_mes(cursor, vigencias_asc, tid)
    fechamento_admin_pendente    = _calcular_fechamento_admin_por_mes(cursor, vigencias_asc, tid)

    # ---- Histórico de Faturamentos — Fornecedor do tenant ----
    cursor.execute("""
        SELECT fat.id, fat.fornecedor_id, fat.mes_ref, fat.numero_baixa,
               fat.quantidade, fat.valor_bruto, fat.perc_fornecedor,
               fat.valor_liquido, fat.data_faturamento, f.razao_social
        FROM faturamentos fat
        JOIN fornecedores f ON f.id = fat.fornecedor_id
        WHERE fat.tenant_id = ?
        ORDER BY fat.mes_ref DESC, f.razao_social
    """, (tid,))
    faturamentos_hist = cursor.fetchall()

    # ---- Histórico de Faturamentos — Entidade Favorecida do tenant ----
    # fat[10] = entidade_nome: nome da entidade favorecida vinculada ao fechamento
    cursor.execute("""
        SELECT fat.id, fat.fornecedor_id, fat.mes_ref, fat.numero_baixa,
               fat.quantidade, fat.valor_bruto, fat.perc_entidade,
               fat.valor_liquido, fat.data_faturamento, f.razao_social,
               COALESCE(e.razao_social, '—') AS entidade_nome
        FROM faturamentos_entidade fat
        JOIN fornecedores f ON f.id = fat.fornecedor_id
        LEFT JOIN entidades e ON e.id = fat.entidade_id
        WHERE fat.tenant_id = ?
        ORDER BY fat.mes_ref DESC, f.razao_social
    """, (tid,))
    faturamentos_entidade_hist = cursor.fetchall()

    # ---- Histórico de Faturamentos — Administração do tenant ----
    cursor.execute("""
        SELECT fat.id, fat.fornecedor_id, fat.mes_ref, fat.numero_baixa,
               fat.quantidade, fat.valor_bruto, fat.perc_admin,
               fat.valor_liquido, fat.data_faturamento, f.razao_social
        FROM faturamentos_admin fat
        JOIN fornecedores f ON f.id = fat.fornecedor_id
        WHERE fat.tenant_id = ?
        ORDER BY fat.mes_ref DESC, f.razao_social
    """, (tid,))
    faturamentos_admin_hist = cursor.fetchall()

    # ---- Total de árvores aprovadas pelo admin do tenant com data_plantio >= 2026-03-01 ----
    # Usa COUNT direto no banco para evitar iteração da lista e garantir precisão da data.
    # data_plantio é coluna DATE em PostgreSQL e TEXT (ISO 8601) em SQLite — a comparação
    # lexicográfica '2026-03-01' funciona em ambos os formatos.
    # Plantios voluntários (tipo='voluntario') são gerenciados pelo Super Admin —
    # não entram no contador de árvores do painel admin do tenant.
    cursor.execute("""
        SELECT COUNT(*)
        FROM plantas_go
        WHERE tenant_id = ?
          AND status = 'aprovado'
          AND data_plantio >= '2026-03-01'
          AND (tipo IS NULL OR tipo != 'voluntario')
    """, (tid,))
    total_arvores = cursor.fetchone()[0]

    conn.close()

    return render_template("admin_painel.html",
                           fornecedores=fornecedores,
                           usuarios=usuarios,
                           plantios=plantios,
                           compras=compras,
                           especies=especies,
                           dados_bancarios=dados_bancarios,
                           entidades=entidades,
                           entidades_ativas=entidades_ativas,
                           entidades_edu=entidades_edu,
                           entidades_edu_admin=entidades_edu_admin,
                           membros=membros,
                           membros_admin=membros_admin,
                           percentuais=percentuais,
                           fechamento_pendente=fechamento_pendente,
                           fechamento_entidade_pendente=fechamento_entidade_pendente,
                           fechamento_admin_pendente=fechamento_admin_pendente,
                           faturamentos_hist=faturamentos_hist,
                           faturamentos_entidade_hist=faturamentos_entidade_hist,
                           faturamentos_admin_hist=faturamentos_admin_hist,
                           busca=busca,
                           tipo=tipo,
                           plantio_status=plantio_status,
                           contagem_plantios=contagem_plantios,
                           total_arvores=total_arvores)


# ===================== ROTA ADMIN: MAPA DE PLANTIOS =====================
# Exibe mapa interativo (Leaflet.js + OpenStreetMap) com marcadores de todos
# os plantios aprovados que possuem coordenadas (latitude e longitude).
@app.route("/admin/mapa")
def admin_mapa():
    if not session.get("admin"):
        return redirect("/admin/login")

    conn   = get_db()
    cursor = conn.cursor()

    # Busca plantios aprovados com coordenadas — inclui todos os campos de foto legados.
    # Garante isolamento: exibe somente plantios do tenant logado.
    tid = get_tid()
    cursor.execute("""
        SELECT pg.id, pg.data_plantio, pg.especie, pg.municipio, pg.bairro,
               pg.latitude, pg.longitude,
               pg.foto_plantio, pg.foto_1, pg.foto_2, pg.foto_3,
               u.nome AS nome_usuario
        FROM plantas_go pg
        JOIN usuarios u ON u.id = pg.responsavel_id
        WHERE pg.tenant_id = ?
          AND pg.status = 'aprovado'
          AND pg.data_plantio >= '2026-03-01'
          AND pg.latitude IS NOT NULL
          AND pg.longitude IS NOT NULL
          AND (pg.tipo IS NULL OR pg.tipo != 'voluntario')
        ORDER BY pg.data_plantio DESC
    """, (tid,))
    plantios_mapa = cursor.fetchall()

    # Busca fotos dos acompanhamentos ilimitados para todos os plantios do mapa
    acomp_fotos = {}
    if plantios_mapa:
        pids = [p[0] for p in plantios_mapa]
        placeholders = ','.join(['?'] * len(pids))
        cursor.execute(
            f"SELECT plantio_id, foto FROM acompanhamentos WHERE plantio_id IN ({placeholders}) AND foto IS NOT NULL ORDER BY plantio_id, id",
            pids
        )
        for row in cursor.fetchall():
            acomp_fotos.setdefault(row[0], []).append(row[1])

    conn.close()

    import json

    def _foto_url(val):
        if not val:
            return None
        return val if val.startswith("http") else f"/static/uploads/{val}"

    marcadores = []
    for p in plantios_mapa:
        # Valida e converte coordenadas — pula o registro se inválidas para não travar o mapa.
        # Protege contra strings malformadas, vazias ou valores fora do range geográfico.
        try:
            lat = float(p[5])
            lng = float(p[6])
        except (ValueError, TypeError):
            continue
        if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
            continue

        # p[7]=foto_plantio  p[8]=foto_1  p[9]=foto_2  p[10]=foto_3  p[11]=nome_usuario
        fotos = [u for u in [_foto_url(p[7]), _foto_url(p[8]), _foto_url(p[9]), _foto_url(p[10])] if u]
        fotos += [_foto_url(f) for f in acomp_fotos.get(p[0], []) if f]

        # Converte data para o padrão brasileiro DD/MM/AA
        data_raw = str(p[1]) if p[1] else ""
        try:
            data_br = datetime.strptime(data_raw, "%Y-%m-%d").strftime("%d/%m/%y") if data_raw else ""
        except ValueError:
            data_br = data_raw

        marcadores.append({
            "id":        p[0],
            "data":      data_br,
            "especie":   p[2] or "",
            "municipio": p[3] or "",
            "bairro":    p[4] or "",
            "lat":       lat,
            "lng":       lng,
            "fotos":     fotos,
            "nome":      p[11] or "",
        })

    return render_template("admin_mapa.html",
                           marcadores_json=json.dumps(marcadores, ensure_ascii=False),
                           total=len(marcadores))


# ===================== ROTA ADMIN: CADASTRAR NOVO FORNECEDOR =====================
# Cria um fornecedor diretamente pelo painel do admin, sem exigir auto-cadastro.
# O tenant_id vem da sessão do admin — garante isolamento multi-tenant.
# Senha é opcional: se não informada, é gerada aleatoriamente (fornecedor redefine depois).
@app.route("/admin/fornecedor/cadastrar", methods=["POST"])
def admin_cadastrar_fornecedor():
    if not session.get("admin"):
        return redirect("/admin/login")

    tid = get_tid()
    if not tid:
        flash("Sessão inválida. Faça login novamente.", "erro")
        return redirect("/admin/login")

    # Lê todos os campos do formulário de cadastro
    razao_social = request.form.get("razao_social", "").strip()
    cnpj         = request.form.get("cnpj", "").strip()
    whatsapp     = request.form.get("whatsapp", "").strip()
    email        = request.form.get("email", "").strip()
    cidade       = request.form.get("cidade", "").strip()
    uf           = request.form.get("uf", "").strip().upper()
    maps_link    = request.form.get("maps_link", "").strip()
    tipo_planta  = request.form.get("tipo_planta", "Nativas").strip()

    # Valida campos obrigatórios
    if not razao_social or not cnpj or not whatsapp:
        flash("Razão Social, CNPJ e WhatsApp são obrigatórios.", "erro")
        return redirect("/admin/painel?tipo=fornecedores")

    # Senha: usa a informada ou gera uma aleatória (fornecedor redefine pelo painel deles)
    senha_raw = request.form.get("senha", "").strip()
    senha_hash = generate_password_hash(senha_raw if senha_raw else secrets.token_hex(16))

    conn   = get_db()
    cursor = conn.cursor()

    try:
        # Insere com tenant_id do admin — vincula ao projeto correto
        cursor.execute("""
            INSERT INTO fornecedores
                (razao_social, cnpj, whatsapp, tipo_planta, maps_link, senha, cidade, uf, email, tenant_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (razao_social, cnpj, whatsapp, tipo_planta, maps_link, senha_hash, cidade, uf, email, tid))
        conn.commit()

        # Gera QR Code do CNPJ e tenta enviar por email se informado
        gerar_qrcode(cnpj)
        if email:
            enviar_qrcode_email(email, razao_social, cnpj)

        flash(f"Fornecedor '{razao_social}' cadastrado com sucesso!", "sucesso")
    except Exception:
        flash("CNPJ já cadastrado ou erro ao salvar. Verifique os dados.", "erro")
    finally:
        conn.close()

    return redirect("/admin/painel?tipo=fornecedores")


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

    # Atualiza todos os campos editáveis do fornecedor — senha não é alterada aqui.
    # AND tenant_id garante isolamento: não afeta fornecedores de outros tenants.
    cursor.execute("""
        UPDATE fornecedores
        SET razao_social = ?, cnpj = ?, whatsapp = ?, cidade = ?, uf = ?,
            tipo_planta = ?, maps_link = ?, email = ?
        WHERE id = ? AND tenant_id = ?
    """, (razao_social, cnpj, whatsapp, cidade, uf, tipo_planta, maps_link, email, fid, get_tid()))
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
    # AND tenant_id: impede excluir fornecedores de outros tenants
    cursor.execute("DELETE FROM fornecedores WHERE id = ? AND tenant_id = ?", (fid, get_tid()))
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

    # Lê o status atual e inverte: 1 → 0 (inativar) ou 0 → 1 (reativar).
    # AND tenant_id: garante isolamento — só afeta fornecedores do tenant logado.
    cursor.execute(
        "SELECT ativo FROM fornecedores WHERE id = ? AND tenant_id = ?",
        (fid, get_tid())
    )
    row = cursor.fetchone()
    if row:
        novo_status = 0 if (row[0] == 1 or row[0] is None) else 1
        cursor.execute(
            "UPDATE fornecedores SET ativo = ? WHERE id = ? AND tenant_id = ?",
            (novo_status, fid, get_tid())
        )
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
    # entidade_educacional_id: obrigatório quando valor=0 (doação escolar)
    ent_edu_id = request.form.get("entidade_educacional_id") or None
    if ent_edu_id:
        ent_edu_id = int(ent_edu_id)

    if not nome:
        flash("Informe o nome da planta.", "erro")
        return redirect("/admin/painel?tipo=especies")

    try:
        valor = float(valor_str)
    except ValueError:
        valor = 50.0

    conn   = get_db()
    cursor = conn.cursor()
    # Inclui tenant_id e entidade_educacional_id (nullable para espécies pagas)
    cursor.execute(
        "INSERT INTO especies_plantas (nome, tipo, valor, entidade_educacional_id, tenant_id) VALUES (?, ?, ?, ?, ?)",
        (nome, tipo, valor, ent_edu_id, get_tid())
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
    # entidade_educacional_id: vínculo da doação com a entidade educacional
    ent_edu_id = request.form.get("entidade_educacional_id") or None
    if ent_edu_id:
        ent_edu_id = int(ent_edu_id)

    try:
        valor = float(valor_str)
    except ValueError:
        valor = 50.0

    conn   = get_db()
    cursor = conn.cursor()
    # Filtro tenant_id garante que somente espécies do tenant logado são editadas
    cursor.execute(
        "UPDATE especies_plantas SET nome = ?, tipo = ?, valor = ?, entidade_educacional_id = ? WHERE id = ? AND tenant_id = ?",
        (nome, tipo, valor, ent_edu_id, eid, get_tid())
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
    # tenant_id garante que somente espécies do tenant logado podem ser excluídas
    cursor.execute("DELETE FROM especies_plantas WHERE id = ? AND tenant_id = ?", (eid, get_tid()))
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

            # Evita duplicatas pelo nome dentro do mesmo tenant
            cursor.execute(
                "SELECT id FROM especies_plantas WHERE nome = ? AND tenant_id = ?",
                (nome, get_tid())
            )
            if not cursor.fetchone():
                # Insere espécie vinculada ao tenant logado
                cursor.execute(
                    "INSERT INTO especies_plantas (nome, tipo, valor, tenant_id) VALUES (?, ?, ?, ?)",
                    (nome, tipo, valor, get_tid())
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

    # Upload do QR Code PIX para o Cloudinary
    qrcode_novo = None
    arquivo_qr  = request.files.get("qrcode_pix")
    if arquivo_qr and arquivo_qr.filename:
        ext = arquivo_qr.filename.rsplit(".", 1)[-1].lower()
        if ext in {"jpg", "jpeg", "png"}:
            qrcode_novo = upload_cloudinary(arquivo_qr, pasta="plantando-vida/pix")

    conn   = get_db()
    cursor = conn.cursor()
    # Filtra pelo tenant_id para garantir upsert isolado por tenant
    cursor.execute(
        "SELECT id, qrcode_pix FROM dados_bancarios WHERE tenant_id = ? LIMIT 1",
        (get_tid(),)
    )
    existente = cursor.fetchone()

    # Mantém o QR Code anterior se nenhum novo foi enviado
    qrcode_final = qrcode_novo or (existente[1] if existente else None)

    if existente:
        cursor.execute("""
            UPDATE dados_bancarios
            SET nome_empresarial=?, banco=?, conta=?, agencia=?, chave_pix=?, qrcode_pix=?
            WHERE id=? AND tenant_id=?
        """, (nome_empresarial, banco, conta, agencia, chave_pix, qrcode_final, existente[0], get_tid()))
    else:
        # Insere registro bancário vinculado ao tenant logado
        cursor.execute("""
            INSERT INTO dados_bancarios (nome_empresarial, banco, conta, agencia, chave_pix, qrcode_pix, tenant_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (nome_empresarial, banco, conta, agencia, chave_pix, qrcode_final, get_tid()))

    conn.commit()
    conn.close()

    flash("Dados bancários salvos com sucesso.", "sucesso")
    return redirect("/admin/painel?tipo=dados_bancarios")


# ===================== ROTA ADMIN: SALVAR MEMBRO DA ADMINISTRAÇÃO =====================
# Cadastra um CPF como membro autorizado a adquirir em Local Credenciado.
# FK: dados_bancarios_id → dados_bancarios.id (1 registro por tenant).
@app.route("/admin/membro-admin/salvar", methods=["POST"])
def admin_salvar_membro_admin():
    if not session.get("admin"):
        return redirect("/admin/login")

    nome    = request.form.get("nome", "").strip()
    cpf_raw = request.form.get("cpf", "")
    # Normaliza CPF para somente dígitos — mesmo padrão da tabela membros
    cpf     = re.sub(r"\D", "", cpf_raw)

    if not nome:
        flash("O campo Nome é obrigatório.", "erro")
        return redirect("/admin/painel?tipo=dados_bancarios")

    if len(cpf) != 11:
        flash("CPF inválido. Informe os 11 dígitos.", "erro")
        return redirect("/admin/painel?tipo=dados_bancarios")

    conn   = get_db()
    cursor = conn.cursor()

    # Busca o id de dados_bancarios do tenant para manter a FK explícita
    cursor.execute("SELECT id FROM dados_bancarios WHERE tenant_id = ? LIMIT 1", (get_tid(),))
    db_row             = cursor.fetchone()
    dados_bancarios_id = db_row[0] if db_row else None

    # Impede CPF duplicado no mesmo tenant
    cursor.execute(
        "SELECT id FROM membros_admin WHERE cpf = ? AND tenant_id = ?",
        (cpf, get_tid())
    )
    if cursor.fetchone():
        conn.close()
        flash("Este CPF já está cadastrado como membro da administração.", "erro")
        return redirect("/admin/painel?tipo=dados_bancarios")

    cursor.execute(
        "INSERT INTO membros_admin (dados_bancarios_id, nome, cpf, tenant_id) VALUES (?, ?, ?, ?)",
        (dados_bancarios_id, nome, cpf, get_tid())
    )
    conn.commit()
    conn.close()

    flash(f"Membro '{nome}' cadastrado com sucesso.", "sucesso")
    return redirect("/admin/painel?tipo=dados_bancarios")


# ===================== ROTA ADMIN: EXCLUIR MEMBRO DA ADMINISTRAÇÃO =====================
# Remove o membro pelo id, garantindo isolamento de tenant.
@app.route("/admin/membro-admin/<int:mid>/excluir", methods=["POST"])
def admin_excluir_membro_admin(mid):
    if not session.get("admin"):
        return redirect("/admin/login")

    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM membros_admin WHERE id = ? AND tenant_id = ?",
        (mid, get_tid())
    )
    conn.commit()
    conn.close()

    flash("Membro removido da administração.", "sucesso")
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

    # Upload do QR Code da entidade favorecida para o Cloudinary
    qrcode_novo = None
    arquivo_qr  = request.files.get("qrcode_pix_ent")
    if arquivo_qr and arquivo_qr.filename:
        ext = arquivo_qr.filename.rsplit(".", 1)[-1].lower()
        if ext in {"jpg", "jpeg", "png"}:
            qrcode_novo = upload_cloudinary(arquivo_qr, pasta="plantando-vida/pix")

    conn   = get_db()
    cursor = conn.cursor()
    # Filtra pelo tenant_id para upsert isolado por tenant
    cursor.execute(
        "SELECT id, qrcode_pix FROM dados_bancarios_entidade WHERE tenant_id = ? LIMIT 1",
        (get_tid(),)
    )
    existente = cursor.fetchone()

    # Mantém o QR Code anterior se nenhum novo foi enviado
    qrcode_final = qrcode_novo or (existente[1] if existente else None)

    if existente:
        cursor.execute("""
            UPDATE dados_bancarios_entidade
            SET nome_empresarial=?, banco=?, conta=?, agencia=?, chave_pix=?, qrcode_pix=?
            WHERE id=? AND tenant_id=?
        """, (nome_empresarial, banco, conta, agencia, chave_pix, qrcode_final, existente[0], get_tid()))
    else:
        # Insere registro da entidade favorecida vinculado ao tenant logado
        cursor.execute("""
            INSERT INTO dados_bancarios_entidade (nome_empresarial, banco, conta, agencia, chave_pix, qrcode_pix, tenant_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (nome_empresarial, banco, conta, agencia, chave_pix, qrcode_final, get_tid()))

    conn.commit()
    conn.close()

    flash("Dados bancários da entidade salvos com sucesso.", "sucesso")
    return redirect("/admin/painel?tipo=fechamento_entidade")


# ===================== ROTA ADMIN: SALVAR ENTIDADE FAVORECIDA (UNIFICADO) =====================
# Cria ou atualiza uma entidade, incluindo dados bancários e status de ativa.
# Campo oculto "id" vazio → INSERT; preenchido → UPDATE.
# O QR Code PIX é salvo em static/pix/ com prefixo "ent_" + uuid.
@app.route("/admin/entidade/salvar", methods=["POST"])
def admin_salvar_entidade():
    if not session.get("admin"):
        return redirect("/admin/login")

    eid          = request.form.get("id", "").strip()
    razao_social = request.form.get("razao_social", "").strip()
    cnpj         = request.form.get("cnpj", "").strip()
    whatsapp     = request.form.get("whatsapp", "").strip()
    banco        = request.form.get("banco", "").strip()
    agencia      = request.form.get("agencia", "").strip()
    conta        = request.form.get("conta", "").strip()
    chave_pix    = request.form.get("chave_pix", "").strip()
    ativa        = 1 if request.form.get("ativa") == "sim" else 0

    if not razao_social:
        flash("O campo Razão Social é obrigatório.", "erro")
        return redirect("/admin/painel?tipo=entidades")

    conn   = get_db()
    cursor = conn.cursor()

    # --- QR Code PIX ---
    # Busca QR code atual (se for edição) para manter caso nenhum novo seja enviado.
    # Filtro tenant_id garante que somente entidades do tenant logado são acessadas.
    qrcode_atual = None
    if eid:
        cursor.execute(
            "SELECT qrcode_pix FROM entidades WHERE id = ? AND tenant_id = ?",
            (int(eid), get_tid())
        )
        row = cursor.fetchone()
        qrcode_atual = row[0] if row else None

    # Upload do QR Code PIX da entidade para o Cloudinary
    qrcode_final = qrcode_atual
    qrfile = request.files.get("qrcode_pix")
    if qrfile and qrfile.filename:
        ext = os.path.splitext(secure_filename(qrfile.filename))[1].lower()
        if ext.lstrip(".") in {"jpg", "jpeg", "png"}:
            qrcode_final = upload_cloudinary(qrfile, pasta="plantando-vida/pix")

    if eid:
        # Atualiza entidade existente — garante que pertence ao tenant logado
        cursor.execute("""
            UPDATE entidades
            SET razao_social=?, cnpj=?, whatsapp=?,
                banco=?, agencia=?, conta=?, chave_pix=?, qrcode_pix=?, ativa=?
            WHERE id=? AND tenant_id=?
        """, (razao_social, cnpj, whatsapp,
              banco, agencia, conta, chave_pix, qrcode_final, ativa,
              int(eid), get_tid()))
        flash("Entidade atualizada com sucesso.", "sucesso")
    else:
        # Insere nova entidade vinculada ao tenant logado
        cursor.execute("""
            INSERT INTO entidades
                (razao_social, cnpj, whatsapp, banco, agencia, conta, chave_pix, qrcode_pix, ativa, tenant_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (razao_social, cnpj, whatsapp,
              banco, agencia, conta, chave_pix, qrcode_final, ativa, get_tid()))
        flash("Entidade cadastrada com sucesso.", "sucesso")

    conn.commit()
    conn.close()
    return redirect("/admin/painel?tipo=entidades")


# ===================== ROTA ADMIN: EXCLUIR ENTIDADE FAVORECIDA =====================
# Remove permanentemente a entidade do banco.
# Acesso restrito ao administrador (session["admin"]).
@app.route("/admin/entidade/<int:eid>/excluir", methods=["POST"])
def admin_excluir_entidade(eid):
    if not session.get("admin"):
        return redirect("/admin/login")

    conn   = get_db()
    cursor = conn.cursor()
    # tenant_id protege contra exclusão de dados de outros tenants
    cursor.execute("DELETE FROM entidades WHERE id = ? AND tenant_id = ?", (eid, get_tid()))
    conn.commit()
    conn.close()

    flash("Entidade excluída.", "sucesso")
    return redirect("/admin/painel?tipo=entidades")


# ===================== ROTA ADMIN: SALVAR ENTIDADE EDUCACIONAL =====================
# Cria ou atualiza uma entidade educacional (escola, ONG, creche, instituição parceira).
# Tabela separada de entidades (APAEs financeiras) — sem vínculo com faturamento.
# Campo oculto "id" vazio → INSERT; preenchido → UPDATE.
# QR Code PIX salvo em static/pix/ com prefixo "edu_" + uuid.
@app.route("/admin/entidade-educacional/salvar", methods=["POST"])
def admin_salvar_entidade_edu():
    if not session.get("admin"):
        return redirect("/admin/login")

    eid          = request.form.get("id", "").strip()
    razao_social = request.form.get("razao_social", "").strip()
    cnpj         = request.form.get("cnpj", "").strip()
    whatsapp     = request.form.get("whatsapp", "").strip()
    banco        = request.form.get("banco", "").strip()
    agencia      = request.form.get("agencia", "").strip()
    conta        = request.form.get("conta", "").strip()
    chave_pix    = request.form.get("chave_pix", "").strip()
    ativa        = 1 if request.form.get("ativa") == "sim" else 0

    if not razao_social:
        flash("O campo Razão Social é obrigatório.", "erro")
        return redirect("/admin/painel?tipo=entidade_educacional")

    # Processa upload do QR Code PIX
    conn   = get_db()
    cursor = conn.cursor()

    qrcode_final = None
    arquivo_qr   = request.files.get("qrcode_pix")
    if arquivo_qr and arquivo_qr.filename:
        import uuid as _uuid
        ext          = os.path.splitext(arquivo_qr.filename)[1].lower()
        nome_arquivo = f"edu_{_uuid.uuid4().hex[:12]}{ext}"
        pasta_pix    = os.path.join(app.root_path, "static", "pix")
        os.makedirs(pasta_pix, exist_ok=True)
        arquivo_qr.save(os.path.join(pasta_pix, nome_arquivo))
        qrcode_final = nome_arquivo

    if eid:
        # UPDATE: mantém QR Code existente se nenhum novo foi enviado
        # Filtro tenant_id garante que somente entidades do tenant logado são alteradas
        if qrcode_final:
            cursor.execute("""
                UPDATE entidades_educacionais
                SET razao_social=?, cnpj=?, whatsapp=?,
                    banco=?, agencia=?, conta=?, chave_pix=?, qrcode_pix=?, ativa=?
                WHERE id=? AND tenant_id=?
            """, (razao_social, cnpj, whatsapp,
                  banco, agencia, conta, chave_pix, qrcode_final, ativa, int(eid), get_tid()))
        else:
            cursor.execute("""
                UPDATE entidades_educacionais
                SET razao_social=?, cnpj=?, whatsapp=?,
                    banco=?, agencia=?, conta=?, chave_pix=?, ativa=?
                WHERE id=? AND tenant_id=?
            """, (razao_social, cnpj, whatsapp,
                  banco, agencia, conta, chave_pix, ativa, int(eid), get_tid()))
        flash("Entidade educacional atualizada com sucesso.", "sucesso")
    else:
        # INSERT vinculado ao tenant logado
        cursor.execute("""
            INSERT INTO entidades_educacionais
                (razao_social, cnpj, whatsapp, banco, agencia, conta, chave_pix, qrcode_pix, ativa, tenant_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (razao_social, cnpj, whatsapp,
              banco, agencia, conta, chave_pix, qrcode_final, ativa, get_tid()))
        flash("Entidade educacional cadastrada com sucesso.", "sucesso")

    conn.commit()
    conn.close()
    return redirect("/admin/painel?tipo=entidade_educacional")


# ===================== ROTA ADMIN: EXCLUIR ENTIDADE EDUCACIONAL =====================
# Remove permanentemente a entidade educacional do banco.
@app.route("/admin/entidade-educacional/<int:eid>/excluir", methods=["POST"])
def admin_excluir_entidade_edu(eid):
    if not session.get("admin"):
        return redirect("/admin/login")

    conn   = get_db()
    cursor = conn.cursor()
    # tenant_id protege contra exclusão de dados de outros tenants
    cursor.execute(
        "DELETE FROM entidades_educacionais WHERE id = ? AND tenant_id = ?",
        (eid, get_tid())
    )
    conn.commit()
    conn.close()

    flash("Entidade educacional excluída.", "sucesso")
    return redirect("/admin/painel?tipo=entidade_educacional")


# ===================== ROTA ADMIN: SALVAR MEMBRO =====================
# Cria um novo membro vinculado a uma Entidade Educacional.
# O CPF é armazenado somente em dígitos (normalizado) para comparação neutra
# na validação do login — evita divergência de formatação.
# Acesso restrito ao administrador do tenant.
@app.route("/admin/membro/salvar", methods=["POST"])
def admin_salvar_membro():
    if not session.get("admin"):
        return redirect("/admin/login")

    nome                    = request.form.get("nome", "").strip()
    cpf_raw                 = request.form.get("cpf", "")
    # Normaliza CPF para somente dígitos — comparação neutra de formatação no login
    cpf                     = re.sub(r"\D", "", cpf_raw)
    entidade_educacional_id = request.form.get("entidade_educacional_id", "").strip()

    # Validações obrigatórias
    if not nome:
        flash("O campo Nome é obrigatório.", "erro")
        return redirect("/admin/painel?tipo=entidade_educacional")

    if len(cpf) != 11:
        flash("CPF inválido. Informe os 11 dígitos.", "erro")
        return redirect("/admin/painel?tipo=entidade_educacional")

    if not entidade_educacional_id:
        flash("Selecione a Entidade Educacional.", "erro")
        return redirect("/admin/painel?tipo=entidade_educacional")

    conn   = get_db()
    cursor = conn.cursor()

    # Verifica se o CPF já está cadastrado como membro nesta entidade e tenant
    cursor.execute(
        "SELECT id FROM membros WHERE cpf = ? AND entidade_educacional_id = ? AND tenant_id = ?",
        (cpf, int(entidade_educacional_id), get_tid())
    )
    if cursor.fetchone():
        conn.close()
        flash("Este CPF já está cadastrado como membro dessa entidade.", "erro")
        return redirect("/admin/painel?tipo=entidade_educacional")

    # Insere o membro vinculado ao tenant logado
    cursor.execute(
        """INSERT INTO membros (nome, cpf, entidade_educacional_id, tenant_id)
           VALUES (?, ?, ?, ?)""",
        (nome, cpf, int(entidade_educacional_id), get_tid())
    )
    conn.commit()
    conn.close()

    flash(f"Membro '{nome}' cadastrado com sucesso.", "sucesso")
    return redirect("/admin/painel?tipo=entidade_educacional")


# ===================== ROTA ADMIN: EXCLUIR MEMBRO =====================
# Remove permanentemente o membro do banco.
# Filtro tenant_id garante que somente membros do tenant logado podem ser excluídos.
@app.route("/admin/membro/<int:mid>/excluir", methods=["POST"])
def admin_excluir_membro(mid):
    if not session.get("admin"):
        return redirect("/admin/login")

    conn   = get_db()
    cursor = conn.cursor()
    # AND tenant_id protege contra exclusão cruzada entre tenants
    cursor.execute(
        "DELETE FROM membros WHERE id = ? AND tenant_id = ?",
        (mid, get_tid())
    )
    conn.commit()
    conn.close()

    flash("Membro removido.", "sucesso")
    return redirect("/admin/painel?tipo=entidade_educacional")


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

    # Determina o tenant da requisição pública via g.tenant_id (detectado em before_request)
    tid = getattr(g, "tenant_id", None) or 1

    # Monta a query dinamicamente conforme os filtros recebidos.
    # tenant_id garante que cada projeto exibe apenas suas próprias espécies.
    # LOWER() + LIKE no campo tipo garante tolerância a variações de capitalização.
    if tipo and busca:
        cursor.execute(
            "SELECT id, nome, tipo, valor FROM especies_plantas WHERE LOWER(tipo) LIKE LOWER(?) AND nome LIKE ? AND tenant_id = ? ORDER BY nome",
            (f"%{tipo}%", f"%{busca}%", tid)
        )
    elif tipo:
        cursor.execute(
            "SELECT id, nome, tipo, valor FROM especies_plantas WHERE LOWER(tipo) LIKE LOWER(?) AND tenant_id = ? ORDER BY nome",
            (f"%{tipo}%", tid)
        )
    elif busca:
        cursor.execute(
            "SELECT id, nome, tipo, valor FROM especies_plantas WHERE nome LIKE ? AND tenant_id = ? ORDER BY nome",
            (f"%{busca}%", tid)
        )
    else:
        # Sem filtros: retorna as primeiras 30 do tenant — não sobrecarrega a resposta
        cursor.execute(
            "SELECT id, nome, tipo, valor FROM especies_plantas WHERE tenant_id = ? ORDER BY nome LIMIT 30",
            (tid,)
        )

    plantas = [{"id": r[0], "nome": r[1], "tipo": r[2], "valor": r[3]} for r in cursor.fetchall()]
    conn.close()
    return jsonify({"plantas": plantas})


# ===================== ROTA FINALIZAR COMPRA =====================
# Salva a compra na tabela compras e envia email de confirmação ao usuário.
# Acesso restrito a usuários logados.
# ===================== ROTAS DO CARRINHO DE COMPRAS =====================
# O carrinho é armazenado na sessão Flask como lista de dicts.
# Cada item: {item_id, fornecedor_id, fornecedor_nome, especie_nome, tipo_planta, valor}.

@app.route("/carrinho/count")
def carrinho_count():
    # Endpoint leve para o JS sincronizar o badge do carrinho ao carregar a página.
    total = len(session.get("carrinho", []))
    return jsonify({"total": total})


@app.route("/carrinho/resumo")
def carrinho_resumo():
    # Retorna contagem e valor total do carrinho — usado para sincronizar a barra flutuante
    # da página de doações escolares ao recarregar sem perder o total acumulado.
    carrinho = session.get("carrinho", [])
    total    = sum(i["valor"] for i in carrinho)
    return jsonify({"total_itens": len(carrinho), "total": total})


@app.route("/carrinho/adicionar", methods=["POST"])
def carrinho_adicionar():
    # Adiciona um item ao carrinho via AJAX (JSON). Retorna total de itens.
    if "usuario_id" not in session:
        return jsonify({"ok": False, "erro": "Login necessário"}), 401

    dados = request.get_json()
    if not dados or not dados.get("especie_nome"):
        return jsonify({"ok": False, "erro": "Dados incompletos"}), 400

    item = {
        "item_id":                str(uuid.uuid4())[:8],
        "fornecedor_id":          dados.get("fornecedor_id"),
        "fornecedor_nome":        dados.get("fornecedor_nome", ""),
        "especie_nome":           dados.get("especie_nome", ""),
        "tipo_planta":            dados.get("tipo_planta", ""),
        "valor":                  float(dados.get("valor", 0)),
        # Presente somente em doações escolares — identifica a entidade educacional receptora
        "entidade_educacional_id": dados.get("entidade_educacional_id"),
    }

    # session["carrinho"] precisa ser reatribuída para o Flask detectar a mudança
    carrinho = list(session.get("carrinho", []))
    carrinho.append(item)
    session["carrinho"] = carrinho
    session.modified    = True

    return jsonify({"ok": True, "total_itens": len(carrinho)})


@app.route("/carrinho/remover", methods=["POST"])
def carrinho_remover():
    # Remove um item pelo item_id via AJAX (JSON). Retorna total restante.
    if "usuario_id" not in session:
        return jsonify({"ok": False}), 401

    dados   = request.get_json() or {}
    item_id = dados.get("item_id")

    carrinho = [i for i in session.get("carrinho", []) if i["item_id"] != item_id]
    session["carrinho"] = carrinho
    session.modified    = True

    return jsonify({"ok": True, "total_itens": len(carrinho)})


@app.route("/carrinho")
def carrinho_view():
    # Exibe a página do carrinho com itens, dados bancários e form de finalização.
    if "usuario_id" not in session:
        return redirect("/login")

    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT nome_empresarial, banco, conta, agencia, chave_pix, qrcode_pix FROM dados_bancarios LIMIT 1")
    dados_bancarios = cursor.fetchone()
    conn.close()

    carrinho = session.get("carrinho", [])
    total    = sum(i["valor"] for i in carrinho)

    # ID da entidade escolar gravado quando o usuário entrou na página de doações;
    # usado para construir o link "Continuar" no template do carrinho.
    origem_escolar_id = session.get("origem_escolar_id")

    return render_template("carrinho.html",
                           carrinho=carrinho,
                           dados_bancarios=dados_bancarios,
                           total=total,
                           origem_escolar_id=origem_escolar_id)


@app.route("/carrinho/finalizar", methods=["POST"])
def carrinho_finalizar():
    # Cria um registro em compras por item, salva comprovante único e envia email.
    if "usuario_id" not in session:
        return redirect("/login")

    carrinho = session.get("carrinho", [])
    if not carrinho:
        flash("Seu carrinho está vazio.", "erro")
        return redirect("/plantio/credenciado")

    # Um comprovante para todo o pedido
    comprovante = salvar_foto("comprovante")

    conn   = get_db()
    cursor = conn.cursor()

    # Identifica o tenant da sessão do usuário para vincular cada compra ao projeto correto
    tid_publico = getattr(g, "tenant_id", None) or 1

    for item in carrinho:
        # INSERT com tenant_id para garantir isolamento multi-tenant nas compras do carrinho
        cursor.execute("""
            INSERT INTO compras (
                usuario_id, fornecedor_id, especie_nome, tipo_planta, valor,
                comprovante, status, entidade_educacional_id, tenant_id
            ) VALUES (?, ?, ?, ?, ?, ?, 'em_analise', ?, ?)
        """, (
            session["usuario_id"],
            item.get("fornecedor_id"),
            item["especie_nome"],
            item["tipo_planta"],
            item["valor"],
            comprovante,
            item.get("entidade_educacional_id"),
            tid_publico,
        ))

    conn.commit()

    cursor.execute("SELECT nome, email FROM usuarios WHERE id = ?", (session["usuario_id"],))
    usuario = cursor.fetchone()
    conn.close()

    total_pedido = sum(i["valor"] for i in carrinho)
    qtd          = len(carrinho)

    # Email de confirmação com resumo de todos os itens do pedido
    if usuario:
        nome_usuario, email_usuario = usuario
        itens_html = "".join([
            f"""<tr>
                <td style="padding:8px;border-bottom:1px solid #e5e7eb;">
                    {'🍎' if i['tipo_planta']=='Frutífera' else '🌳'} {i['especie_nome']}
                </td>
                <td style="padding:8px;border-bottom:1px solid #e5e7eb;color:#6b7280;">
                    {i['fornecedor_nome']}
                </td>
                <td style="padding:8px;border-bottom:1px solid #e5e7eb;text-align:right;
                            font-weight:bold;color:#15803d;">
                    R$ {i['valor']:.2f}
                </td>
            </tr>"""
            for i in carrinho
        ])

        corpo_html = f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:32px 0;">
  <tr><td align="center">
    <table width="540" cellpadding="0" cellspacing="0"
           style="background:#fff;border-radius:16px;overflow:hidden;
                  box-shadow:0 4px 24px rgba(0,0,0,.08);max-width:540px;width:100%;">
      <tr>
        <td style="background:#166534;padding:28px 36px;text-align:center;">
          <p style="margin:0 0 4px;font-size:20px;font-weight:700;color:#fff;">Plantando Vida</p>
          <p style="margin:0;font-size:12px;color:#bbf7d0;letter-spacing:1px;text-transform:uppercase;">
            Pedido Recebido
          </p>
        </td>
      </tr>
      <tr>
        <td style="padding:32px 36px 24px;">
          <p style="margin:0 0 6px;font-size:20px;font-weight:700;color:#111827;">
            Ola, {nome_usuario}!
          </p>
          <p style="margin:0 0 24px;font-size:14px;color:#4b5563;line-height:1.6;">
            Recebemos seu pedido com <strong>{qtd} {'item' if qtd==1 else 'itens'}</strong>.
            Nossa equipe irá conferir o comprovante e confirmar em breve.
          </p>
          <table width="100%" cellpadding="0" cellspacing="0"
                 style="border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;font-size:13px;">
            <thead>
              <tr style="background:#f9fafb;">
                <th style="padding:10px 8px;text-align:left;color:#374151;">Planta</th>
                <th style="padding:10px 8px;text-align:left;color:#374151;">Fornecedor</th>
                <th style="padding:10px 8px;text-align:right;color:#374151;">Valor</th>
              </tr>
            </thead>
            <tbody>{itens_html}</tbody>
            <tfoot>
              <tr style="background:#f0fdf4;">
                <td colspan="2" style="padding:10px 8px;font-weight:700;color:#166534;">Total</td>
                <td style="padding:10px 8px;text-align:right;font-weight:700;color:#166534;font-size:15px;">
                  R$ {total_pedido:.2f}
                </td>
              </tr>
            </tfoot>
          </table>
          <div style="background:#fefce8;border-radius:8px;padding:14px;
                      border-left:4px solid #ca8a04;margin-top:20px;">
            <p style="margin:0;font-size:13px;color:#92400e;">
              <strong>Aguarde a validacao do pagamento.</strong>
              Voce sera notificado por email assim que confirmado.
            </p>
          </div>
        </td>
      </tr>
      <tr>
        <td style="padding:16px 36px 24px;text-align:center;border-top:1px solid #e5e7eb;">
          <p style="margin:0;font-size:11px;color:#9ca3af;">
            Email automatico — nao responda.<br>
            &copy; 2026 Plantando Vida. Todos os direitos reservados.
          </p>
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body></html>"""

        enviar_email(email_usuario,
                     f"Pedido recebido ({qtd} {'item' if qtd==1 else 'itens'}) — Plantando Vida",
                     corpo_html)

    # Limpa o carrinho após finalizar
    session.pop("carrinho", None)
    session.modified = True

    flash(f"Pedido realizado! {qtd} {'item aguardando' if qtd==1 else 'itens aguardando'} validação.", "sucesso")
    return redirect("/plantios/pendentes")


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

    # tenant_id vincula a compra ao projeto do tenant detectado em before_request
    cursor.execute("""
        INSERT INTO compras (usuario_id, fornecedor_id, especie_nome, tipo_planta, valor, comprovante, status, tenant_id)
        VALUES (?, ?, ?, ?, ?, ?, 'em_analise', ?)
    """, (
        session["usuario_id"], fornecedor_id, especie_nome, tipo_planta, valor, comprovante,
        getattr(g, "tenant_id", None) or 1
    ))
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

    # Busca dados para o email antes de atualizar (valor determina se é doação)
    cursor.execute("""
        SELECT c.especie_nome, c.tipo_planta, c.valor, u.nome, u.email
        FROM compras c JOIN usuarios u ON u.id = c.usuario_id
        WHERE c.id = ?
    """, (cid,))
    dados = cursor.fetchone()

    # Doações (valor = 0) pulam a etapa de voucher/fornecedor:
    # status vai direto para 'retirado' permitindo ao usuário realizar o plantio imediatamente.
    # Compras pagas ficam em 'aprovado' até o fornecedor validar o voucher.
    eh_doacão = dados and float(dados[2]) == 0.0
    novo_status = "retirado" if eh_doacão else "aprovado"

    cursor.execute(
        "UPDATE compras SET status = ? WHERE id = ? AND tenant_id = ?",
        (novo_status, cid, get_tid())
    )
    conn.commit()
    conn.close()

    # Notifica o usuário por email
    if dados:
        especie, tipo, valor, nome_usuario, email_usuario = dados
        if eh_doacão:
            # Email específico para doação: instrui a realizar o plantio
            corpo_html = f"""
            <div style="font-family:sans-serif;max-width:520px;margin:auto;background:#f0fdf4;
                        border-radius:12px;padding:32px;border:1px solid #bbf7d0">
                <div style="text-align:center;margin-bottom:20px">
                    <span style="font-size:48px">🎁</span>
                    <h1 style="color:#15803d;font-size:20px;margin:12px 0 4px">Doação Aprovada!</h1>
                    <p style="color:#4b7a58;font-size:14px">Olá, {nome_usuario}! Sua muda foi disponibilizada.</p>
                </div>
                <div style="background:#fff;border-radius:8px;padding:16px;border-left:4px solid #16a34a;margin-bottom:16px">
                    <p style="margin:4px 0;font-size:14px;color:#111827">🌿 <strong>{especie}</strong> — {tipo}</p>
                    <p style="margin:4px 0;font-size:14px;color:#111827">🎁 Doação Gratuita</p>
                </div>
                <p style="font-size:13px;color:#4b5563;text-align:center">
                    Acesse o aplicativo para registrar o plantio da sua muda. Bom plantio! 🌱
                </p>
            </div>
            """
            enviar_email(email_usuario, "🎁 Doação aprovada — Plantando Vida", corpo_html)
        else:
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

    # tenant_id garante que somente compras do tenant logado são reprovadas
    cursor.execute(
        "UPDATE compras SET status = 'reprovado' WHERE id = ? AND tenant_id = ?",
        (cid, get_tid())
    )
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
    # tenant_id garante isolamento: somente compras do tenant logado são alteradas
    cursor.execute(
        "UPDATE compras SET status = 'em_analise' WHERE id = ? AND tenant_id = ?",
        (cid, get_tid())
    )
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
# tid: tenant_id para isolamento multi-tenant — filtra compras e fornecedores do tenant.
def _calcular_fechamento_pendente(cursor, vigencias, tipo='fornecedor', tid=None):
    # Mapeia tipo → coluna de faturamento em compras e índice do percentual em vigencias
    _col = {
        'fornecedor': ('faturamento_id',          1),
        'entidade':   ('faturamento_entidade_id', 2),
        'admin':      ('faturamento_admin_id',    3),
    }
    fat_col, perc_idx = _col[tipo]

    # Filtro de tenant: quando tid é fornecido, restringe ao projeto do admin logado
    tenant_filter = "AND c.tenant_id = ?" if tid else ""
    params        = (tid,) if tid else ()

    cursor.execute(f"""
        SELECT c.id, c.fornecedor_id, c.valor, c.data_validacao,
               SUBSTR(COALESCE(c.data_validacao, ''), 1, 7) AS mes_ref,
               f.razao_social, f.whatsapp
        FROM compras c
        JOIN fornecedores f ON f.id = c.fornecedor_id
        WHERE c.status = 'retirado'
          AND (c.{fat_col} IS NULL OR c.{fat_col} = 0)
          {tenant_filter}
        ORDER BY c.fornecedor_id, mes_ref
    """, params)
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


# ===================== HELPER: FECHAMENTO ENTIDADE TOTALIZADO POR MÊS =====================
# Mesma lógica do helper de Admin, mas usa perc_entidade (índice 2) e faturamento_entidade_id.
# Retorna lista ordenada por mes_ref ASC com totais do mês e lista de fornecedores contribuintes.
# tid: tenant_id para isolamento multi-tenant — filtra compras do projeto do admin logado.
def _calcular_fechamento_entidade_por_mes(cursor, vigencias, tid=None):
    tenant_filter = "AND c.tenant_id = ?" if tid else ""
    params        = (tid,) if tid else ()
    cursor.execute(f"""
        SELECT c.id, c.fornecedor_id, c.valor, c.data_validacao,
               SUBSTR(COALESCE(c.data_validacao, ''), 1, 7) AS mes_ref,
               f.razao_social
        FROM compras c
        JOIN fornecedores f ON f.id = c.fornecedor_id
        WHERE c.status = 'retirado'
          AND (c.faturamento_entidade_id IS NULL OR c.faturamento_entidade_id = 0)
          {tenant_filter}
        ORDER BY mes_ref, c.fornecedor_id
    """, params)
    rows = cursor.fetchall()

    meses = {}
    for row in rows:
        _id, forn_id, valor, data_valid, mes_ref, razao = row
        if not mes_ref:
            mes_ref = "sem_data"

        if mes_ref not in meses:
            try:
                ano, mes  = mes_ref.split("-")
                num_baixa = f"{mes}{ano}"
            except ValueError:
                num_baixa = "000000"
            meses[mes_ref] = {
                "mes_ref":       mes_ref,
                "numero_baixa":  num_baixa,
                "quantidade":    0,
                "valor_bruto":   0.0,
                "valor_liquido": 0.0,
                "percs":         set(),
                "fornecedores":  {},
            }

        perc = _perc_vigente(data_valid, vigencias, 2)   # índice 2 = perc_entidade
        val  = valor or 0.0

        meses[mes_ref]["quantidade"]    += 1
        meses[mes_ref]["valor_bruto"]   += val
        meses[mes_ref]["valor_liquido"] += val * perc / 100
        meses[mes_ref]["percs"].add(perc)

        if forn_id not in meses[mes_ref]["fornecedores"]:
            meses[mes_ref]["fornecedores"][forn_id] = {
                "fornecedor_id": forn_id,
                "razao_social":  razao,
                "quantidade":    0,
                "valor_bruto":   0.0,
                "valor_liquido": 0.0,
            }
        meses[mes_ref]["fornecedores"][forn_id]["quantidade"]    += 1
        meses[mes_ref]["fornecedores"][forn_id]["valor_bruto"]   += val
        meses[mes_ref]["fornecedores"][forn_id]["valor_liquido"] += val * perc / 100

    resultado = []
    for m in meses.values():
        percs = sorted(m["percs"])
        m["perc_display"] = "/".join(f"{p:.0f}%" for p in percs)
        del m["percs"]
        m["fornecedores"] = sorted(m["fornecedores"].values(), key=lambda x: x["razao_social"])
        resultado.append(m)

    resultado.sort(key=lambda x: x["mes_ref"])
    return resultado


# ===================== HELPER: FECHAMENTO ADMIN TOTALIZADO POR MÊS =====================
# Agrupa todas as compras pendentes de admin por mês (ignorando fornecedor na chave),
# totalizando quantidade, valor_bruto e valor_liquido de todos os fornecedores do período.
# Internamente mantém a lista de fornecedores contribuintes para exibição no template.
# Retorna lista ordenada por mes_ref ASC, cada item é um dict com:
#   mes_ref, numero_baixa, quantidade, valor_bruto, valor_liquido, perc_display, fornecedores[]
def _calcular_fechamento_admin_por_mes(cursor, vigencias, tid=None):
    # tid: tenant_id para isolamento multi-tenant — filtra compras do projeto do admin logado.
    tenant_filter = "AND c.tenant_id = ?" if tid else ""
    params        = (tid,) if tid else ()
    cursor.execute(f"""
        SELECT c.id, c.fornecedor_id, c.valor, c.data_validacao,
               SUBSTR(COALESCE(c.data_validacao, ''), 1, 7) AS mes_ref,
               f.razao_social
        FROM compras c
        JOIN fornecedores f ON f.id = c.fornecedor_id
        WHERE c.status = 'retirado'
          AND (c.faturamento_admin_id IS NULL OR c.faturamento_admin_id = 0)
          {tenant_filter}
        ORDER BY mes_ref, c.fornecedor_id
    """, params)
    rows = cursor.fetchall()

    meses = {}
    for row in rows:
        _id, forn_id, valor, data_valid, mes_ref, razao = row
        if not mes_ref:
            mes_ref = "sem_data"

        # Cria bucket do mês se ainda não existe
        if mes_ref not in meses:
            try:
                ano, mes  = mes_ref.split("-")
                num_baixa = f"{mes}{ano}"
            except ValueError:
                num_baixa = "000000"
            meses[mes_ref] = {
                "mes_ref":       mes_ref,
                "numero_baixa":  num_baixa,
                "quantidade":    0,
                "valor_bruto":   0.0,
                "valor_liquido": 0.0,
                "percs":         set(),
                "fornecedores":  {},   # forn_id → dict com subtotais
            }

        perc = _perc_vigente(data_valid, vigencias, 3)   # índice 3 = perc_admin
        val  = valor or 0.0

        # Acumula totais do mês
        meses[mes_ref]["quantidade"]    += 1
        meses[mes_ref]["valor_bruto"]   += val
        meses[mes_ref]["valor_liquido"] += val * perc / 100
        meses[mes_ref]["percs"].add(perc)

        # Acumula subtotais por fornecedor (para lista detalhada no card)
        if forn_id not in meses[mes_ref]["fornecedores"]:
            meses[mes_ref]["fornecedores"][forn_id] = {
                "fornecedor_id": forn_id,
                "razao_social":  razao,
                "quantidade":    0,
                "valor_bruto":   0.0,
                "valor_liquido": 0.0,
            }
        meses[mes_ref]["fornecedores"][forn_id]["quantidade"]    += 1
        meses[mes_ref]["fornecedores"][forn_id]["valor_bruto"]   += val
        meses[mes_ref]["fornecedores"][forn_id]["valor_liquido"] += val * perc / 100

    resultado = []
    for m in meses.values():
        percs = sorted(m["percs"])
        m["perc_display"] = "/".join(f"{p:.0f}%" for p in percs)
        del m["percs"]
        m["fornecedores"] = sorted(m["fornecedores"].values(), key=lambda x: x["razao_social"])
        resultado.append(m)

    resultado.sort(key=lambda x: x["mes_ref"])
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

    # Campo oculto "id" vem preenchido quando o admin clicou em ✏️ Editar → UPDATE.
    # Caso contrário (campo vazio ou ausente) → INSERT de nova vigência.
    registro_id = request.form.get("id", "").strip()

    conn   = get_db()
    cursor = conn.cursor()

    if registro_id:
        # Atualiza vigência existente — tenant_id garante isolamento entre tenants
        cursor.execute("""
            UPDATE percentuais_vigencia
            SET inicio_vigencia = ?,
                perc_fornecedor = ?,
                perc_entidade   = ?,
                perc_admin      = ?
            WHERE id = ? AND tenant_id = ?
        """, (inicio_vigencia, perc_fornecedor, perc_entidade, perc_admin, int(registro_id), get_tid()))
        flash(f"Vigência atualizada com sucesso (a partir de {inicio_vigencia}).", "sucesso")
    else:
        # Insere nova vigência vinculada ao tenant logado
        cursor.execute("""
            INSERT INTO percentuais_vigencia (inicio_vigencia, perc_fornecedor, perc_entidade, perc_admin, tenant_id)
            VALUES (?, ?, ?, ?, ?)
        """, (inicio_vigencia, perc_fornecedor, perc_entidade, perc_admin, get_tid()))
        flash(f"Vigência a partir de {inicio_vigencia} cadastrada com sucesso.", "sucesso")

    conn.commit()
    conn.close()

    return redirect("/admin/painel?tipo=percentuais")


# ===================== ROTA ADMIN: EXCLUIR PERCENTUAL DE VIGÊNCIA =====================
@app.route("/admin/percentual/<int:pid>/excluir", methods=["POST"])
def admin_percentual_excluir(pid):
    if not session.get("admin"):
        return redirect("/admin/login")

    conn   = get_db()
    cursor = conn.cursor()
    # tenant_id garante que somente vigências do tenant logado são excluídas
    cursor.execute(
        "DELETE FROM percentuais_vigencia WHERE id = ? AND tenant_id = ?",
        (pid, get_tid())
    )
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

    # Busca vigências do tenant logado — ordenadas ASC para aplicar a vigente em cada data
    cursor.execute("""
        SELECT inicio_vigencia, perc_fornecedor, perc_entidade, perc_admin
        FROM percentuais_vigencia
        WHERE tenant_id = ?
        ORDER BY inicio_vigencia ASC
    """, (get_tid(),))
    vigencias = cursor.fetchall()

    # Busca compras pendentes do fornecedor no mês — filtrada pelo tenant logado
    cursor.execute("""
        SELECT id, valor, data_validacao
        FROM compras
        WHERE fornecedor_id = ?
          AND status = 'retirado'
          AND (faturamento_id IS NULL OR faturamento_id = 0)
          AND SUBSTR(COALESCE(data_validacao, ''), 1, 7) = ?
          AND tenant_id = ?
    """, (int(fornecedor_id), mes_ref, get_tid()))
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

    # Insere o faturamento vinculado ao tenant logado
    cursor.execute("""
        INSERT INTO faturamentos
            (fornecedor_id, mes_ref, numero_baixa, quantidade, valor_bruto, perc_fornecedor, valor_liquido, tenant_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (int(fornecedor_id), mes_ref, numero_baixa,
          quantidade, valor_bruto, perc_usado, valor_liquido, get_tid()))

    # Recupera o id do faturamento recém-inserido (compatível com SQLite e PostgreSQL)
    cursor.execute("""
        SELECT id FROM faturamentos
        WHERE fornecedor_id = ? AND mes_ref = ? AND tenant_id = ?
        ORDER BY id DESC LIMIT 1
    """, (int(fornecedor_id), mes_ref, get_tid()))
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
# tid: tenant_id do admin logado — garante isolamento multi-tenant
def _criar_faturamento_tipo(fornecedor_id, mes_ref, tipo, tabela, perc_col, fat_col, campo_idx, entidade_id=None, tid=None):
    conn   = get_db()
    cursor = conn.cursor()

    # Busca vigências filtradas pelo tenant — necessário para percentuais corretos por projeto
    if tid:
        cursor.execute("""
            SELECT inicio_vigencia, perc_fornecedor, perc_entidade, perc_admin
            FROM percentuais_vigencia WHERE tenant_id = ? ORDER BY inicio_vigencia ASC
        """, (tid,))
    else:
        cursor.execute("""
            SELECT inicio_vigencia, perc_fornecedor, perc_entidade, perc_admin
            FROM percentuais_vigencia ORDER BY inicio_vigencia ASC
        """)
    vigencias = cursor.fetchall()

    # Busca compras pendentes — filtrada pelo tenant quando fornecido
    if tid:
        cursor.execute(f"""
            SELECT id, valor, data_validacao FROM compras
            WHERE fornecedor_id = ?
              AND status = 'retirado'
              AND ({fat_col} IS NULL OR {fat_col} = 0)
              AND SUBSTR(COALESCE(data_validacao, ''), 1, 7) = ?
              AND tenant_id = ?
        """, (int(fornecedor_id), mes_ref, tid))
    else:
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

    # Inclui entidade_id e tenant_id no INSERT para isolamento completo
    if entidade_id:
        cursor.execute(f"""
            INSERT INTO {tabela}
                (fornecedor_id, mes_ref, numero_baixa, quantidade, valor_bruto, {perc_col}, valor_liquido, entidade_id, tenant_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (int(fornecedor_id), mes_ref, numero_baixa,
              quantidade, valor_bruto, perc_usado, valor_liquido, int(entidade_id), tid))
    else:
        cursor.execute(f"""
            INSERT INTO {tabela}
                (fornecedor_id, mes_ref, numero_baixa, quantidade, valor_bruto, {perc_col}, valor_liquido, tenant_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (int(fornecedor_id), mes_ref, numero_baixa,
              quantidade, valor_bruto, perc_usado, valor_liquido, tid))

    # Recupera id do faturamento recém-inserido — filtra por tenant para garantir o correto
    if tid:
        cursor.execute(f"""
            SELECT id FROM {tabela} WHERE fornecedor_id = ? AND mes_ref = ? AND tenant_id = ?
            ORDER BY id DESC LIMIT 1
        """, (int(fornecedor_id), mes_ref, tid))
    else:
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
    # entidade_id: pk da entidade favorecida selecionada no formulário de Saldos Pendentes.
    entidade_id_raw = request.form.get("entidade_id", "").strip()
    entidade_id     = int(entidade_id_raw) if entidade_id_raw else None

    if not fornecedor_id or not mes_ref:
        flash("Dados inválidos para faturamento.", "erro")
        return redirect("/admin/painel?tipo=fechamento_entidade")

    if not entidade_id:
        flash("Selecione a Entidade Favorecida antes de faturar.", "erro")
        return redirect("/admin/painel?tipo=fechamento_entidade")

    ok, msg = _criar_faturamento_tipo(
        fornecedor_id, mes_ref,
        tipo        = 'entidade',
        tabela      = 'faturamentos_entidade',
        perc_col    = 'perc_entidade',
        fat_col     = 'faturamento_entidade_id',
        campo_idx   = 2,
        entidade_id = entidade_id,
        tid         = get_tid(),  # isolamento multi-tenant
    )
    flash(f"Faturamento Entidade gerado — {msg}" if ok else msg, "sucesso" if ok else "erro")
    return redirect("/admin/painel?tipo=fechamento_entidade")


# ===================== ROTA ADMIN: CRIAR FATURAMENTO — ENTIDADE (POR MÊS) =====================
# Fatura TODOS os fornecedores com compras pendentes para o mês informado,
# vinculando cada registro à entidade selecionada no dropdown.
@app.route("/admin/faturamento-entidade/criar-mes", methods=["POST"])
def admin_faturamento_entidade_criar_mes():
    if not session.get("admin"):
        return redirect("/admin/login")

    mes_ref         = request.form.get("mes_ref", "").strip()
    entidade_id_raw = request.form.get("entidade_id", "").strip()
    entidade_id     = int(entidade_id_raw) if entidade_id_raw else None

    if not mes_ref:
        flash("Mês inválido para faturamento.", "erro")
        return redirect("/admin/painel?tipo=fechamento_entidade")

    if not entidade_id:
        flash("Selecione a Entidade Favorecida antes de faturar.", "erro")
        return redirect("/admin/painel?tipo=fechamento_entidade")

    # Busca todos os fornecedores distintos com compras pendentes no mês — filtrado por tenant
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT fornecedor_id FROM compras
        WHERE status = 'retirado'
          AND (faturamento_entidade_id IS NULL OR faturamento_entidade_id = 0)
          AND SUBSTR(COALESCE(data_validacao, ''), 1, 7) = ?
          AND tenant_id = ?
    """, (mes_ref, get_tid()))
    fornecedores_ids = [row[0] for row in cursor.fetchall()]
    conn.close()

    if not fornecedores_ids:
        flash("Nenhuma compra pendente para faturar neste período.", "erro")
        return redirect("/admin/painel?tipo=fechamento_entidade")

    total_liquido = 0.0
    total_mudas   = 0
    erros         = []

    for forn_id in fornecedores_ids:
        ok, msg = _criar_faturamento_tipo(
            forn_id, mes_ref,
            tipo        = 'entidade',
            tabela      = 'faturamentos_entidade',
            perc_col    = 'perc_entidade',
            fat_col     = 'faturamento_entidade_id',
            campo_idx   = 2,
            entidade_id = entidade_id,
            tid         = get_tid(),  # isolamento multi-tenant
        )
        if ok:
            partes = msg.split("·")
            try:
                total_mudas   += int(partes[1].strip().split()[0])
                total_liquido += float(partes[2].strip().replace("R$", "").replace(",", ".").strip())
            except (IndexError, ValueError):
                pass
        else:
            erros.append(msg)

    if erros:
        flash(f"Atenção: alguns faturamentos falharam — {'; '.join(erros)}", "erro")
    else:
        try:
            ano, mes = mes_ref.split("-")
            num_baixa = f"{mes}{ano}"
        except ValueError:
            num_baixa = mes_ref
        flash(
            f"Faturamento Entidade — nº Baixa {num_baixa} · "
            f"{total_mudas} muda(s) · R$ {total_liquido:.2f} (total do mês)",
            "sucesso"
        )
    return redirect("/admin/painel?tipo=fechamento_entidade")


# ===================== ROTA ADMIN: CRIAR FATURAMENTO — ADMINISTRAÇÃO (POR MÊS) =====================
# Fatura TODOS os fornecedores com compras pendentes para o mês informado,
# gerando um registro em faturamentos_admin por fornecedor (mesma lógica do helper genérico).
# O admin vê 1 botão "Faturar" por mês e esta rota processa o lote inteiro.
@app.route("/admin/faturamento-admin/criar-mes", methods=["POST"])
def admin_faturamento_admin_criar_mes():
    if not session.get("admin"):
        return redirect("/admin/login")

    mes_ref = request.form.get("mes_ref", "").strip()
    if not mes_ref:
        flash("Mês inválido para faturamento.", "erro")
        return redirect("/admin/painel?tipo=fechamento_admin")

    # Busca todos os fornecedores distintos com compras pendentes no mês — filtrado por tenant
    conn   = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT fornecedor_id FROM compras
        WHERE status = 'retirado'
          AND (faturamento_admin_id IS NULL OR faturamento_admin_id = 0)
          AND SUBSTR(COALESCE(data_validacao, ''), 1, 7) = ?
          AND tenant_id = ?
    """, (mes_ref, get_tid()))
    fornecedores_ids = [row[0] for row in cursor.fetchall()]
    conn.close()

    if not fornecedores_ids:
        flash("Nenhuma compra pendente para faturar neste período.", "erro")
        return redirect("/admin/painel?tipo=fechamento_admin")

    total_liquido = 0.0
    total_mudas   = 0
    erros         = []

    for forn_id in fornecedores_ids:
        ok, msg = _criar_faturamento_tipo(
            forn_id, mes_ref,
            tipo      = 'admin',
            tabela    = 'faturamentos_admin',
            perc_col  = 'perc_admin',
            fat_col   = 'faturamento_admin_id',
            campo_idx = 3,
            tid       = get_tid(),  # isolamento multi-tenant
        )
        if ok:
            # Extrai quantidade e valor do msg: "nº Baixa MMAAAA · N muda(s) · R$ X.XX"
            partes = msg.split("·")
            try:
                total_mudas   += int(partes[1].strip().split()[0])
                total_liquido += float(partes[2].strip().replace("R$", "").replace(",", ".").strip())
            except (IndexError, ValueError):
                pass
        else:
            erros.append(msg)

    if erros:
        flash(f"Atenção: alguns faturamentos falharam — {'; '.join(erros)}", "erro")
    else:
        try:
            ano, mes = mes_ref.split("-")
            num_baixa = f"{mes}{ano}"
        except ValueError:
            num_baixa = mes_ref
        flash(
            f"Faturamento Admin — nº Baixa {num_baixa} · "
            f"{total_mudas} muda(s) · R$ {total_liquido:.2f} (total do mês)",
            "sucesso"
        )
    return redirect("/admin/painel?tipo=fechamento_admin")


# ===================== ROTA PLANTIO VOLUNTÁRIO — CONFIRMAÇÃO =====================
# Exibe a página de confirmação com dois cards (Sim / Não) antes de iniciar o plantio.
# Disponível para qualquer usuário logado — sem restrição de membro.
@app.route("/plantio/voluntario")
def plantio_voluntario():
    if "usuario_id" not in session:
        return redirect("/login")
    if not session.get("termos_aceitos"):
        return redirect("/termos")
    return render_template("plantio_voluntario.html")


# ===================== ROTA PLANTIO VOLUNTÁRIO — INICIAR =====================
# Cria uma compra virtual (valor=0, status='aprovado', tipo_planta='Voluntário')
# e redireciona para o fluxo padrão de 5 etapas em /plantio/iniciar.
# ehDoacao=true (valor=0) pula automaticamente a Etapa 1 (QR Code).
@app.route("/plantio/voluntario/iniciar")
def plantio_voluntario_iniciar():
    if "usuario_id" not in session:
        return redirect("/login")
    if not session.get("termos_aceitos"):
        return redirect("/termos")

    tid = getattr(g, "tenant_id", None) or 1

    conn   = get_db()
    cursor = conn.cursor()

    # Insere compra virtual sem fornecedor nem entidade — tipo marca como voluntário
    cursor.execute("""
        INSERT INTO compras
            (usuario_id, fornecedor_id, especie_nome, tipo_planta,
             valor, comprovante, status, entidade_educacional_id, tenant_id)
        VALUES (?, NULL, ?, ?, 0.0, NULL, 'aprovado', NULL, ?)
    """, (session["usuario_id"], "Muda Voluntária", "Voluntário", tid))
    conn.commit()

    # Recupera o id da compra recém-inserida
    cursor.execute(
        "SELECT id FROM compras WHERE usuario_id = ? ORDER BY id DESC LIMIT 1",
        (session["usuario_id"],)
    )
    row = cursor.fetchone()
    conn.close()

    if not row:
        flash("Erro ao iniciar plantio voluntário. Tente novamente.", "erro")
        return redirect("/dashboard")

    return redirect(f"/plantio/iniciar/{row[0]}")


# ===================== ROTA INICIAR PLANTIO (5 ETAPAS) =====================
# Exibe a página com o fluxo guiado de 5 etapas para registrar o plantio definitivo.
# Acesso restrito ao dono da compra com status 'retirado' e sem plantio já vinculado.
@app.route("/plantio/iniciar/<int:compra_id>")
def plantio_iniciar(compra_id):
    if "usuario_id" not in session:
        return redirect("/login")

    conn   = get_db()
    cursor = conn.cursor()

    # Valida que a compra pertence ao usuário logado e está pronta para plantio.
    # Aceita dois casos:
    #   - status='retirado': compra paga com voucher validado pelo fornecedor
    #   - status='aprovado' AND valor=0: doação aprovada (sem etapa de voucher/fornecedor)
    cursor.execute("""
        SELECT c.id, c.especie_nome, c.tipo_planta, c.valor,
               COALESCE(f.razao_social, 'Fornecedor não informado'),
               f.cidade, f.uf, c.plantio_id
        FROM compras c
        LEFT JOIN fornecedores f ON f.id = c.fornecedor_id
        WHERE c.id = ? AND c.usuario_id = ?
          AND (c.status = 'retirado' OR (c.status = 'aprovado' AND c.valor = 0))
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

    # Validações básicas dos campos obrigatórios.
    # Municipio e bairro são dispensáveis quando há coordenadas GPS (GPS manual de emergência).
    if not compra_id or not especie:
        flash("Preencha todos os campos obrigatórios.", "erro")
        return redirect("/plantios/pendentes")

    # Se GPS foi informado mas município/bairro ficaram vazios, usa fallback descritivo
    if not municipio:
        municipio = "Não informado" if (latitude and longitude) else ""
    if not bairro:
        bairro = "Não informado" if (latitude and longitude) else ""

    # Após fallback, revalida: sem GPS E sem município/bairro é inválido
    if not municipio or not bairro:
        flash("Preencha o município e o bairro, ou capture a localização GPS.", "erro")
        return redirect("/plantios/pendentes")

    conn   = get_db()
    cursor = conn.cursor()

    # Reconfirma que a compra pertence ao usuário e ainda está sem plantio vinculado.
    # Aceita status='retirado' (compra paga, voucher validado) ou
    # status='aprovado' AND valor=0 (doação aprovada sem etapa de voucher).
    # tipo_planta e entidade_educacional_id são usados para gravar o tipo do plantio.
    cursor.execute("""
        SELECT id, fornecedor_id, tipo_planta, entidade_educacional_id FROM compras
        WHERE id = ? AND usuario_id = ? AND plantio_id IS NULL
          AND (status = 'retirado' OR (status = 'aprovado' AND valor = 0))
    """, (int(compra_id), session["usuario_id"]))
    compra = cursor.fetchone()

    if not compra:
        conn.close()
        flash("Operação inválida. A compra não foi encontrada ou já possui plantio.", "erro")
        return redirect("/plantios/pendentes")

    fornecedor_id           = compra[1]
    tipo_planta_compra      = compra[2] or ""
    entidade_educacional_id = compra[3]

    # Deriva o tipo do plantio com base na origem da compra
    if tipo_planta_compra == "Voluntário":
        tipo_plantio = "voluntario"

        # Para plantios voluntários, espécie e tipo da muda são informados pelo voluntário
        especie_voluntario = request.form.get("especie_voluntario", "").strip()
        tipo_muda_voluntario = request.form.get("tipo_voluntario", "Nativa").strip()

        # Usa o valor informado se preenchido; mantém "Muda Voluntária" como fallback
        if especie_voluntario:
            especie = especie_voluntario
        # tipo_muda_voluntario é salvo em tipo_planta (Nativa/Frutífera)
    elif entidade_educacional_id:
        tipo_plantio = "escolar"
        tipo_muda_voluntario = tipo        # usa o tipo da compra escolar
    else:
        tipo_plantio = "credenciado"
        tipo_muda_voluntario = tipo        # usa o tipo da compra credenciada

    # Salva foto ao lado da cova (Etapa 3) — foto_plantio
    # Verifica primeiro o input de câmera; se vazio, usa o input de galeria (temporário/emergência)
    foto_plantio = None
    for campo_foto in ("foto_plantio", "foto_plantio_galeria"):
        if campo_foto in request.files:
            arq = request.files[campo_foto]
            if arq and arq.filename:
                ext = arq.filename.rsplit(".", 1)[-1].lower()
                if ext in EXTENSOES_PERMITIDAS:
                    foto_plantio = upload_cloudinary(arq, pasta="plantando-vida/plantios")
                    break  # usa o primeiro arquivo válido encontrado

    # Salva foto com a planta na cova e regada (Etapa 4) — acompanhamento_1
    # Verifica primeiro o input de câmera; se vazio, usa o input de galeria (temporário/emergência)
    foto_1 = None
    for campo_foto in ("foto_acomp1", "foto_acomp1_galeria"):
        if campo_foto in request.files:
            arq = request.files[campo_foto]
            if arq and arq.filename:
                ext = arq.filename.rsplit(".", 1)[-1].lower()
                if ext in EXTENSOES_PERMITIDAS:
                    foto_1 = upload_cloudinary(arq, pasta="plantando-vida/acompanhamentos")
                    break  # usa o primeiro arquivo válido encontrado

    # Usa data no fuso Brasil (UTC-3) — evita registrar amanhã quando o servidor roda em UTC.
    data_hoje = datetime.now(TZ_BRASIL).strftime("%Y-%m-%d")

    # Voluntário é aprovado automaticamente — não precisa de revisão do admin.
    # Demais tipos iniciam em 'em_analise' para aprovação manual.
    status_plantio = "aprovado" if tipo_plantio == "voluntario" else "em_analise"

    # Insere o plantio definitivo — tipo classifica a origem; tipo_planta = Nativa/Frutífera
    cursor.execute("""
        INSERT INTO plantas_go (
            data_plantio, responsavel_id, especie, municipio, bairro,
            latitude, longitude,
            foto_plantio, foto_1, acompanhamento_1,
            fornecedor_id, status, tenant_id, tipo, tipo_planta
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data_hoje, session["usuario_id"], especie, municipio, bairro,
        latitude, longitude,
        foto_plantio, foto_1, data_hoje if foto_1 else None,
        fornecedor_id, status_plantio,
        getattr(g, "tenant_id", None) or 1,
        tipo_plantio,
        tipo_muda_voluntario
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

    # Voluntário: dados ficam no domínio do Super Admin; usuário volta ao dashboard.
    # Demais tipos: redireciona para a lista de plantios pendentes do usuário.
    if tipo_plantio == "voluntario":
        flash("🌿 Plantio Voluntário registrado com sucesso! Obrigado pela sua contribuição.", "sucesso")
        return redirect("/dashboard")

    flash("Plantio registrado com sucesso! Acompanhe o desenvolvimento da sua muda.", "sucesso")
    return redirect("/plantios/pendentes")


# ===================== ROTA LOGOUT ADMINISTRADOR =====================
# Encerra a sessão do administrador e redireciona para o login.
@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect("/admin/login")

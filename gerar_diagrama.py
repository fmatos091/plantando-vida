"""
Gerador de Diagrama de Casos de Uso — Projeto Plantando Vida
Execute: python gerar_diagrama.py
Saída:   diagrama_casos_uso.pdf
"""

from reportlab.lib.pagesizes import A3, landscape
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.lib.units import cm
import math

# ── Paleta de cores ──────────────────────────────────────────
COR_FUNDO        = colors.HexColor("#f0fdf4")
COR_SISTEMA      = colors.HexColor("#166534")
COR_USUARIO      = colors.HexColor("#1d4ed8")
COR_ADMIN        = colors.HexColor("#7e22ce")
COR_FORNECEDOR   = colors.HexColor("#b45309")
COR_OVAL_FILL    = colors.HexColor("#ffffff")
COR_OVAL_STROKE  = colors.HexColor("#374151")
COR_LINHA        = colors.HexColor("#9ca3af")
COR_TITULO       = colors.HexColor("#111827")

LARGURA, ALTURA = landscape(A3)
MARGEM = 1.2 * cm


# ── Helpers de desenho ───────────────────────────────────────

def boneco(c, x, y, cor, label, tamanho=18):
    """Desenha um ator (stick figure) com rótulo abaixo."""
    r  = tamanho * 0.45
    ty = tamanho * 0.9
    bx = tamanho * 0.5
    by = tamanho * 1.7

    c.setStrokeColor(cor)
    c.setFillColor(colors.white)
    c.setLineWidth(1.5)

    # Cabeça
    c.circle(x, y + ty + r, r, stroke=1, fill=1)
    # Corpo
    c.line(x, y + ty, x, y + ty - by * 0.55)
    # Braços
    c.line(x - bx, y + ty - by * 0.2, x + bx, y + ty - by * 0.2)
    # Perna esquerda
    c.line(x, y + ty - by * 0.55, x - bx * 0.8, y + ty - by)
    # Perna direita
    c.line(x, y + ty - by * 0.55, x + bx * 0.8, y + ty - by)

    # Rótulo
    c.setFillColor(cor)
    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(x, y + ty - by - 10, label)


def oval_caso(c, cx, cy, w, h, texto, cor_stroke=COR_OVAL_STROKE):
    """Desenha um oval de caso de uso com texto multi-linha centralizado."""
    c.setFillColor(COR_OVAL_FILL)
    c.setStrokeColor(cor_stroke)
    c.setLineWidth(1.2)
    c.ellipse(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2, stroke=1, fill=1)

    # Quebra o texto em linhas de no máximo 18 chars
    palavras = texto.split()
    linhas, linha_atual = [], ""
    for p in palavras:
        teste = (linha_atual + " " + p).strip()
        if len(teste) <= 18:
            linha_atual = teste
        else:
            if linha_atual:
                linhas.append(linha_atual)
            linha_atual = p
    if linha_atual:
        linhas.append(linha_atual)

    c.setFillColor(COR_OVAL_STROKE)
    c.setFont("Helvetica", 6.5)
    total = len(linhas)
    for i, linha in enumerate(linhas):
        offset = (i - (total - 1) / 2) * 8
        c.drawCentredString(cx, cy - offset, linha)


def linha(c, x1, y1, x2, y2):
    """Desenha uma linha de associação entre ator e caso de uso."""
    c.setStrokeColor(COR_LINHA)
    c.setLineWidth(0.8)
    c.line(x1, y1, x2, y2)


def retangulo_sistema(c, x, y, w, h, titulo, cor):
    """Desenha o retângulo de fronteira do sistema."""
    c.setFillColor(colors.transparent)
    c.setStrokeColor(cor)
    c.setLineWidth(2)
    c.setDash(6, 3)
    c.rect(x, y, w, h, stroke=1, fill=0)
    c.setDash()
    c.setFillColor(cor)
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(x + w / 2, y + h + 5, titulo)


def secao_header(c, x, y, w, h, titulo, cor):
    """Cabeçalho colorido de cada grupo de casos de uso dentro da fronteira."""
    c.setFillColor(cor)
    c.setStrokeColor(cor)
    c.roundRect(x, y, w, h, 6, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(x + w / 2, y + h / 2 - 4, titulo)


# ── Dados do diagrama ────────────────────────────────────────

ATORES = {
    "usuario":     {"label": "Usuário",     "cor": COR_USUARIO},
    "admin":       {"label": "Admin",       "cor": COR_ADMIN},
    "fornecedor":  {"label": "Fornecedor",  "cor": COR_FORNECEDOR},
}

# (texto, ator_principal, [atores_extras])
CASOS = [
    # ── Autenticação / Conta ──
    ("Cadastrar Conta",            "usuario",    []),
    ("Verificar Email (Token)",    "usuario",    []),
    ("Fazer Login",                "usuario",    []),
    ("Recuperar Senha",            "usuario",    []),
    ("Editar Perfil",              "usuario",    []),
    ("Logout",                     "usuario",    []),

    # ── Compras e Voucher ──
    ("Comprar Muda",               "usuario",    []),
    ("Enviar Comprovante",         "usuario",    []),
    ("Baixar Voucher",             "usuario",    []),

    # ── Plantios ──
    ("Registrar Plantio",          "usuario",    []),
    ("Acompanhar Plantio",         "usuario",    []),
    ("Ver Meus Plantios",          "usuario",    []),

    # ── Admin: Gestão ──
    ("Login Admin",                "admin",      []),
    ("Gerenciar Fornecedores",     "admin",      []),
    ("Gerenciar Usuários",         "admin",      []),
    ("Gerenciar Espécies",         "admin",      []),
    ("Gerenciar Entidades",        "admin",      []),

    # ── Admin: Plantios / Compras ──
    ("Aprovar Plantio",            "admin",      []),
    ("Reprovar Plantio",           "admin",      []),
    ("Aprovar Compra",             "admin",      []),
    ("Reprovar Compra",            "admin",      []),

    # ── Admin: Financeiro ──
    ("Configurar Dados Bancários", "admin",      []),
    ("Definir Vigência %",         "admin",      []),
    ("Gerar Faturamento Fornecedor","admin",     []),
    ("Gerar Faturamento Entidade", "admin",      []),
    ("Gerar Faturamento Admin",    "admin",      []),

    # ── Fornecedor ──
    ("Login Fornecedor",           "fornecedor", []),
    ("Ver Painel Fornecedor",      "fornecedor", []),
    ("Validar Voucher (QR)",       "fornecedor", []),
    ("Atualizar Dados",            "fornecedor", []),
    ("Recuperar Senha Fornecedor", "fornecedor", []),
]

# Grupos visuais: (título, cor, índices_em_CASOS)
GRUPOS = [
    ("Autenticação / Conta",   COR_USUARIO,    list(range(0, 6))),
    ("Compras & Voucher",      COR_USUARIO,    list(range(6, 9))),
    ("Plantios",               COR_USUARIO,    list(range(9, 12))),
    ("Gestão",                 COR_ADMIN,      list(range(12, 17))),
    ("Plantios / Compras",     COR_ADMIN,      list(range(17, 21))),
    ("Financeiro",             COR_ADMIN,      list(range(21, 26))),
    ("Fornecedor",             COR_FORNECEDOR, list(range(26, 31))),
]


# ── Layout principal ─────────────────────────────────────────

def gerar_pdf(nome_arquivo="diagrama_casos_uso.pdf"):
    c = canvas.Canvas(nome_arquivo, pagesize=landscape(A3))
    c.setTitle("Diagrama de Casos de Uso - Plantando Vida")

    # Fundo
    c.setFillColor(COR_FUNDO)
    c.rect(0, 0, LARGURA, ALTURA, stroke=0, fill=1)

    # Título
    c.setFillColor(COR_SISTEMA)
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(LARGURA / 2, ALTURA - 1.5 * cm, "Plantando Vida - Diagrama de Casos de Uso")
    c.setFont("Helvetica", 9)
    c.setFillColor(colors.HexColor("#6b7280"))
    c.drawCentredString(LARGURA / 2, ALTURA - 2.2 * cm, "Sistema de Gestão de Plantios · Usuário · Admin · Fornecedor")

    # ── Dimensões da área de desenho ──
    area_x = MARGEM + 2.5 * cm
    area_y = MARGEM
    area_w = LARGURA - 2 * MARGEM - 5 * cm
    area_h = ALTURA - MARGEM - 3.5 * cm

    # Fronteira do sistema
    retangulo_sistema(c, area_x, area_y, area_w, area_h, "Sistema Plantando Vida", COR_SISTEMA)

    # ── Posicionamento dos grupos em grid ──
    cols       = 4
    rows       = 2
    pad        = 0.4 * cm
    grupo_w    = (area_w - pad * (cols + 1)) / cols
    grupo_h    = (area_h - pad * (rows + 1)) / rows
    oval_w     = grupo_w * 0.76
    oval_h     = 22

    posicoes_ovals = {}  # índice_caso → (cx, cy)

    for g_idx, (titulo_g, cor_g, indices) in enumerate(GRUPOS):
        col = g_idx % cols
        row = g_idx // cols

        gx = area_x + pad + col * (grupo_w + pad)
        gy = area_y + area_h - pad - (row + 1) * grupo_h - row * pad

        # Header do grupo
        secao_header(c, gx, gy + grupo_h - 18, grupo_w, 18, titulo_g, cor_g)

        # Ovals dentro do grupo
        n = len(indices)
        cols_ov = min(n, 2)
        rows_ov = math.ceil(n / cols_ov)
        ov_pad_x = (grupo_w - cols_ov * oval_w) / (cols_ov + 1)
        ov_pad_y = ((grupo_h - 18) - rows_ov * oval_h) / (rows_ov + 1)

        for i, caso_idx in enumerate(indices):
            oc = i % cols_ov
            or_ = i // cols_ov
            cx = gx + ov_pad_x + oc * (oval_w + ov_pad_x) + oval_w / 2
            cy = gy + grupo_h - 18 - ov_pad_y - or_ * (oval_h + ov_pad_y) - oval_h / 2
            oval_caso(c, cx, cy, oval_w, oval_h, CASOS[caso_idx][0], cor_g)
            posicoes_ovals[caso_idx] = (cx, cy)

    # ── Atores à esquerda e à direita ──
    ator_x_esq = MARGEM + 1.0 * cm
    ator_x_dir = LARGURA - MARGEM - 1.0 * cm

    # Usuário (esquerda, centro-alto)
    boneco(c, ator_x_esq, area_y + area_h * 0.62, COR_USUARIO, "Usuário", tamanho=20)
    boneco_y_usuario = area_y + area_h * 0.62 + 20 * 0.9 + 20 * 0.45

    # Admin (esquerda, centro-baixo)
    boneco(c, ator_x_esq, area_y + area_h * 0.22, COR_ADMIN, "Admin", tamanho=20)
    boneco_y_admin = area_y + area_h * 0.22 + 20 * 0.9 + 20 * 0.45

    # Fornecedor (direita)
    boneco(c, ator_x_dir, area_y + area_h * 0.42, COR_FORNECEDOR, "Fornecedor", tamanho=20)
    boneco_y_forn = area_y + area_h * 0.42 + 20 * 0.9 + 20 * 0.45

    # ── Linhas de associação ──
    for caso_idx, (texto, ator, extras) in enumerate(CASOS):
        if caso_idx not in posicoes_ovals:
            continue
        cx, cy = posicoes_ovals[caso_idx]

        if ator == "usuario":
            linha(c, ator_x_esq, boneco_y_usuario, cx - oval_w / 2, cy)
        elif ator == "admin":
            linha(c, ator_x_esq, boneco_y_admin, cx - oval_w / 2, cy)
        elif ator == "fornecedor":
            linha(c, ator_x_dir, boneco_y_forn, cx + oval_w / 2, cy)

    # ── Legenda ──
    leg_x = LARGURA - 5.5 * cm
    leg_y = area_y + 1.5 * cm
    c.setFillColor(colors.white)
    c.setStrokeColor(colors.HexColor("#d1d5db"))
    c.setLineWidth(1)
    c.roundRect(leg_x - 0.3 * cm, leg_y - 0.3 * cm, 4.5 * cm, 3.5 * cm, 6, stroke=1, fill=1)
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(COR_TITULO)
    c.drawString(leg_x, leg_y + 2.8 * cm, "Legenda")
    for i, (label, cor) in enumerate([
        ("Usuário",    COR_USUARIO),
        ("Admin",      COR_ADMIN),
        ("Fornecedor", COR_FORNECEDOR),
    ]):
        yy = leg_y + 2.1 * cm - i * 0.7 * cm
        c.setFillColor(cor)
        c.rect(leg_x, yy, 10, 10, stroke=0, fill=1)
        c.setFillColor(COR_TITULO)
        c.setFont("Helvetica", 7.5)
        c.drawString(leg_x + 14, yy + 1, label)

    c.save()
    print(f"PDF gerado: {nome_arquivo}")
    print(f"   {len(CASOS)} casos de uso | {len(GRUPOS)} grupos | 3 atores")


if __name__ == "__main__":
    gerar_pdf()

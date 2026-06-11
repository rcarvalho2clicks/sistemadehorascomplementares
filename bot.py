import sqlite3
import os
import uuid
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# ─── CONFIG ─────────────────────────────────────────────────────
# 1. Crie um bot em https://t.me/BotFather
# 2. Coloque o token aqui OU exporte como: export BOT_TOKEN=seu_token
TOKEN = os.environ.get("BOT_TOKEN") or "SEU_TOKEN_AQUI"
DATABASE = os.path.join(os.path.dirname(__file__), "database.db")
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "static", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Estados do usuário (armazenados em memória - volátil)
user_state = {}  # chat_id -> {'state': ..., 'data': {...}}

# ─── BANCO ──────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS alunos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            curso TEXT NOT NULL,
            turma TEXT NOT NULL,
            semestre TEXT NOT NULL,
            ra TEXT NOT NULL UNIQUE,
            email TEXT,
            telegram_id INTEGER,
            senha TEXT NOT NULL,
            data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS admin (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT NOT NULL UNIQUE,
            senha TEXT NOT NULL,
            telegram_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS submissao (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            aluno_id INTEGER NOT NULL,
            evento TEXT NOT NULL,
            descricao TEXT,
            horas REAL NOT NULL,
            arquivo TEXT,
            status TEXT DEFAULT 'pendente',
            admin_nota TEXT,
            data_envio TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            data_revisao TIMESTAMP,
            FOREIGN KEY (aluno_id) REFERENCES alunos(id)
        );
    ''')
    # Admin padrão
    admin_exists = conn.execute("SELECT id FROM admin WHERE usuario = 'admin'").fetchone()
    if not admin_exists:
        conn.execute("INSERT INTO admin (usuario, senha) VALUES (?, ?)", ('admin', 'admin123'))
    conn.commit()
    conn.close()

# ─── HELPERS ────────────────────────────────────────────────────
def aluno_por_telegram(telegram_id):
    conn = get_db()
    a = conn.execute("SELECT * FROM alunos WHERE telegram_id = ?", (telegram_id,)).fetchone()
    conn.close()
    return a

def admin_por_telegram(telegram_id):
    conn = get_db()
    a = conn.execute("SELECT * FROM admin WHERE telegram_id = ?", (telegram_id,)).fetchone()
    conn.close()
    return a

def aluno_por_ra_senha(ra, senha):
    conn = get_db()
    a = conn.execute("SELECT * FROM alunos WHERE ra = ? AND senha = ?", (ra, senha)).fetchone()
    conn.close()
    return a

def admin_por_usuario_senha(usuario, senha):
    conn = get_db()
    a = conn.execute("SELECT * FROM admin WHERE usuario = ? AND senha = ?", (usuario, senha)).fetchone()
    conn.close()
    return a

# ─── MENUS ──────────────────────────────────────────────────────
def menu_principal():
    kb = [
        [InlineKeyboardButton("🎓 Sou Aluno", callback_data="menu_aluno")],
        [InlineKeyboardButton("🔐 Sou Admin", callback_data="menu_admin")],
    ]
    return InlineKeyboardMarkup(kb)

def menu_aluno(conectado=False):
    if not conectado:
        kb = [
            [InlineKeyboardButton("📝 Cadastrar", callback_data="aluno_cadastro")],
            [InlineKeyboardButton("🔑 Login", callback_data="aluno_login")],
            [InlineKeyboardButton("🔙 Voltar", callback_data="menu_voltar")],
        ]
    else:
        kb = [
            [InlineKeyboardButton("📋 Minhas Submissões", callback_data="aluno_submissoes")],
            [InlineKeyboardButton("➕ Nova Submissão", callback_data="aluno_nova")],
            [InlineKeyboardButton("📊 Meu Resumo", callback_data="aluno_resumo")],
            [InlineKeyboardButton("🚪 Sair", callback_data="aluno_logout")],
            [InlineKeyboardButton("🔙 Voltar", callback_data="menu_voltar")],
        ]
    return InlineKeyboardMarkup(kb)

def menu_admin(conectado=False):
    if not conectado:
        kb = [
            [InlineKeyboardButton("🔑 Login Admin", callback_data="admin_login")],
            [InlineKeyboardButton("🔙 Voltar", callback_data="menu_voltar")],
        ]
    else:
        kb = [
            [InlineKeyboardButton("📋 Pendentes", callback_data="admin_pendentes")],
            [InlineKeyboardButton("📊 Dashboard", callback_data="admin_dashboard")],
            [InlineKeyboardButton("🚪 Sair", callback_data="admin_logout")],
            [InlineKeyboardButton("🔙 Voltar", callback_data="menu_voltar")],
        ]
    return InlineKeyboardMarkup(kb)

# ─── HANDLERS ───────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎓 *Bem-vindo ao Sistema de Horas Complementares!*\n\n"
        "Aqui você pode:\n"
        "✅ Enviar comprovantes de horas complementares\n"
        "✅ Acompanhar o status das suas solicitações\n"
        "✅ Admin pode aprovar ou rejeitar as submissões\n\n"
        "Escolha uma opção abaixo:",
        parse_mode="Markdown",
        reply_markup=menu_principal()
    )

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id
    user = user_state.get(chat_id, {})

    # ── MENU PRINCIPAL ─────────────────────────────────────────
    if data == "menu_voltar":
        user_state[chat_id] = {}
        await query.edit_message_text(
            "🎓 *Sistema de Horas Complementares*\n\nEscolha uma opção:",
            parse_mode="Markdown",
            reply_markup=menu_principal()
        )

    elif data == "menu_aluno":
        aluno = aluno_por_telegram(chat_id)
        if aluno:
            user_state[chat_id] = {'tipo': 'aluno', 'aluno_id': aluno['id'], 'aluno_nome': aluno['nome']}
            txt = f"👋 Olá, *{aluno['nome']}*!\nRA: {aluno['ra']} — {aluno['curso']}"
        else:
            txt = "🎓 *Área do Aluno*"
        await query.edit_message_text(txt, parse_mode="Markdown",
            reply_markup=menu_aluno(conectado=bool(aluno)))

    elif data == "menu_admin":
        admin = admin_por_telegram(chat_id)
        if admin:
            user_state[chat_id] = {'tipo': 'admin', 'admin_id': admin['id']}
            txt = f"🔐 *Admin logado* ({admin['usuario']})"
        else:
            txt = "🔐 *Área do Administrador*"
        await query.edit_message_text(txt, parse_mode="Markdown",
            reply_markup=menu_admin(conectado=bool(admin)))

    # ── ALUNO: CADASTRO ───────────────────────────────────────
    elif data == "aluno_cadastro":
        user_state[chat_id] = {'state': 'aguardando_nome'}
        await query.edit_message_text(
            "📝 *Cadastro de Aluno*\n\n"
            "Vamos precisar de algumas informações.\n\n"
            "Passo 1/6: Qual o seu *nome completo*?",
            parse_mode="Markdown"
        )

    elif data == "aluno_login":
        user_state[chat_id] = {'state': 'aguardando_login_ra'}
        await query.edit_message_text(
            "🔑 *Login do Aluno*\n\n"
            "Digite seu *RA*:",
            parse_mode="Markdown"
        )

    elif data == "aluno_logout":
        user_state.pop(chat_id, None)
        await query.edit_message_text(
            "✅ Você saiu da conta de aluno.",
            reply_markup=menu_principal()
        )

    # ── ALUNO: DASHBOARD ──────────────────────────────────────
    elif data == "aluno_submissoes":
        await mostrar_submissoes(query, chat_id)

    elif data == "aluno_resumo":
        await mostrar_resumo(query, chat_id)

    elif data == "aluno_nova":
        aluno = aluno_por_telegram(chat_id)
        if not aluno:
            await query.edit_message_text("❌ Você precisa estar logado como aluno primeiro!",
                reply_markup=menu_aluno(False))
            return
        user_state[chat_id] = {'state': 'aguardando_evento', 'tipo': 'aluno', 'aluno_id': aluno['id']}
        await query.edit_message_text(
            "📝 *Nova Submissão*\n\n"
            "Passo 1/3: Qual o *nome do evento/atividade* que você participou?\n"
            "Ex: Palestra de IA, Curso de Python, Semana Acadêmica...",
            parse_mode="Markdown"
        )

    # ── ADMIN ─────────────────────────────────────────────────
    elif data == "admin_login":
        user_state[chat_id] = {'state': 'aguardando_admin_usuario'}
        await query.edit_message_text(
            "🔐 *Login Admin*\n\n"
            "Digite seu *usuário*:",
            parse_mode="Markdown"
        )

    elif data == "admin_logout":
        user_state.pop(chat_id, None)
        await query.edit_message_text(
            "✅ Você saiu da conta de admin.",
            reply_markup=menu_principal()
        )

    elif data == "admin_pendentes":
        await mostrar_pendentes(query)

    elif data == "admin_dashboard":
        await mostrar_dashboard_admin(query)

    # ── ADMIN: APROVAR / REJEITAR ────────────────────────────
    elif data.startswith("aprovar_"):
        sub_id = int(data.split("_")[1])
        conn = get_db()
        conn.execute("UPDATE submissao SET status = 'aprovado', data_revisao = ? WHERE id = ?",
                     (datetime.now().isoformat(), sub_id))
        conn.commit()
        conn.close()
        await query.edit_message_text("✅ *Submissão aprovada com sucesso!*", parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Ver pendentes", callback_data="admin_pendentes")],
                [InlineKeyboardButton("🔙 Menu Admin", callback_data="menu_admin")]
            ]))

    elif data.startswith("rejeitar_"):
        sub_id = int(data.split("_")[1])
        user_state[chat_id] = {'state': 'aguardando_motivo_rejeicao', 'sub_id': sub_id}
        await query.edit_message_text(
            "❌ *Rejeitar Submissão*\n\n"
            "Digite o *motivo da rejeição* (ou /cancelar para voltar):",
            parse_mode="Markdown"
        )

    # Aprovar/rejeitar diretamente da listagem
    elif data.startswith("apr_"):
        sub_id = int(data.split("_")[1])
        conn = get_db()
        conn.execute("UPDATE submissao SET status = 'aprovado', data_revisao = ? WHERE id = ?",
                     (datetime.now().isoformat(), sub_id))
        conn.commit()
        conn.close()
        await query.answer("✅ Aprovado!")
        await mostrar_pendentes(query)

    elif data.startswith("rej_"):
        sub_id = int(data.split("_")[1])
        user_state[chat_id] = {'state': 'aguardando_motivo_rejeicao', 'sub_id': sub_id}
        await query.edit_message_text(
            "❌ *Rejeitar Submissão*\n\n"
            "Digite o *motivo* (ou /cancelar):",
            parse_mode="Markdown"
        )

async def mostrar_submissoes(query, chat_id):
    aluno = aluno_por_telegram(chat_id)
    if not aluno:
        await query.edit_message_text("❌ Aluno não encontrado. Faça login novamente.",
            reply_markup=menu_aluno(False))
        return

    conn = get_db()
    subs = conn.execute(
        "SELECT * FROM submissao WHERE aluno_id = ? ORDER BY data_envio DESC",
        (aluno['id'],)
    ).fetchall()
    conn.close()

    if not subs:
        await query.edit_message_text(
            "📋 *Você ainda não tem submissões.*\n\n"
            "Clique em *Nova Submissão* para começar!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Nova Submissão", callback_data="aluno_nova")],
                [InlineKeyboardButton("🔙 Voltar", callback_data="menu_aluno")]
            ])
        )
        return

    msg = f"📋 *Suas Submissões ({len(subs)})*\n\n"
    for s in subs[:10]:
        status_emoji = {"pendente": "⏳", "aprovado": "✅", "rejeitado": "❌"}
        emoji = status_emoji.get(s['status'], "❓")
        msg += f"{emoji} *{s['evento']}* — {s['horas']}h\n"
        msg += f"   Status: {s['status'].upper()}\n"
        if s['admin_nota']:
            msg += f"   Obs: {s['admin_nota']}\n"
        msg += f"   📅 {s['data_envio'][:10]}\n\n"

    if len(subs) > 10:
        msg += f"... e mais {len(subs) - 10} submissões.\n\n"

    await query.edit_message_text(msg, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Nova Submissão", callback_data="aluno_nova")],
            [InlineKeyboardButton("🔙 Voltar", callback_data="menu_aluno")]
        ]))

async def mostrar_resumo(query, chat_id):
    aluno = aluno_por_telegram(chat_id)
    if not aluno:
        return

    conn = get_db()
    total_h = conn.execute(
        "SELECT COALESCE(SUM(horas), 0) FROM submissao WHERE aluno_id = ? AND status = 'aprovado'",
        (aluno['id'],)
    ).fetchone()[0]
    pend = conn.execute(
        "SELECT COUNT(*) FROM submissao WHERE aluno_id = ? AND status = 'pendente'",
        (aluno['id'],)
    ).fetchone()[0]
    aprov = conn.execute(
        "SELECT COUNT(*) FROM submissao WHERE aluno_id = ? AND status = 'aprovado'",
        (aluno['id'],)
    ).fetchone()[0]
    rej = conn.execute(
        "SELECT COUNT(*) FROM submissao WHERE aluno_id = ? AND status = 'rejeitado'",
        (aluno['id'],)
    ).fetchone()[0]
    conn.close()

    await query.edit_message_text(
        f"📊 *Resumo do Aluno*\n\n"
        f"👤 *{aluno['nome']}*\n"
        f"📚 {aluno['curso']} — {aluno['turma']} ({aluno['semestre']})\n"
        f"🔢 RA: {aluno['ra']}\n\n"
        f"✅ Horas aprovadas: *{total_h}h*\n"
        f"⏳ Pendentes: {pend}\n"
        f"✅ Aprovadas: {aprov}\n"
        f"❌ Rejeitadas: {rej}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Voltar", callback_data="menu_aluno")]
        ]))

async def mostrar_pendentes(query):
    conn = get_db()
    pendentes = conn.execute('''
        SELECT s.*, a.nome, a.curso, a.turma, a.semestre, a.ra
        FROM submissao s
        JOIN alunos a ON s.aluno_id = a.id
        WHERE s.status = 'pendente'
        ORDER BY s.data_envio DESC
    ''').fetchall()
    conn.close()

    if not pendentes:
        await query.edit_message_text(
            "✅ *Nenhuma submissão pendente!*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Menu Admin", callback_data="menu_admin")]
            ])
        )
        return

    for s in pendentes:
        txt = (
            f"🆔 *#{s['id']}* — ⏳ Pendente\n"
            f"👤 *{s['nome']}*\n"
            f"📚 {s['curso']} — {s['turma']} ({s['semestre']})\n"
            f"🔢 RA: {s['ra']}\n\n"
            f"📌 *{s['evento']}*\n"
            f"⏱️ {s['horas']}h\n"
            f"📅 {s['data_envio'][:19]}"
        )
        kb = [
            [
                InlineKeyboardButton("✅ Aprovar", callback_data=f"apr_{s['id']}"),
                InlineKeyboardButton("❌ Rejeitar", callback_data=f"rej_{s['id']}"),
            ],
            [
                InlineKeyboardButton("🔄 Atualizar", callback_data="admin_pendentes"),
                InlineKeyboardButton("🔙 Menu", callback_data="menu_admin"),
            ],
        ]
        if s['arquivo']:
            # Try to send file if it exists
            filepath = os.path.join(UPLOAD_DIR, s['arquivo'])
            if os.path.exists(filepath):
                try:
                    await query.message.reply_document(
                        document=open(filepath, 'rb'),
                        caption=txt,
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(kb)
                    )
                    continue
                except:
                    pass
        await query.message.reply_text(txt, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb))

    await query.message.reply_text("📋 Fim da lista de pendentes.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Atualizar", callback_data="admin_pendentes")],
            [InlineKeyboardButton("🔙 Menu Admin", callback_data="menu_admin")]
        ]))

async def mostrar_dashboard_admin(query):
    conn = get_db()
    stats = conn.execute('''
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status='pendente' THEN 1 ELSE 0 END) as pendentes,
            SUM(CASE WHEN status='aprovado' THEN 1 ELSE 0 END) as aprovados,
            SUM(CASE WHEN status='rejeitado' THEN 1 ELSE 0 END) as rejeitados
        FROM submissao
    ''').fetchone()
    conn.close()

    await query.edit_message_text(
        "📊 *Dashboard do Admin*\n\n"
        f"📌 Pendentes: *{stats['pendentes'] or 0}*\n"
        f"✅ Aprovados: *{stats['aprovados'] or 0}*\n"
        f"❌ Rejeitados: *{stats['rejeitados'] or 0}*\n"
        f"📦 Total: *{stats['total'] or 0}*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Ver Pendentes", callback_data="admin_pendentes")],
            [InlineKeyboardButton("🔙 Menu Admin", callback_data="menu_admin")]
        ]))

# ─── RECEBER MENSAGENS DE TEXTO ─────────────────────────────────
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    text = update.message.text.strip()
    state = user_state.get(chat_id, {})

    # ── CADASTRO DE ALUNO ─────────────────────────────────────
    if state.get('state') == 'aguardando_nome':
        user_state[chat_id]['nome'] = text
        user_state[chat_id]['state'] = 'aguardando_curso'
        await update.message.reply_text(
            f"✅ Nome: *{text}*\n\n"
            "Passo 2/6: Qual o seu *curso*?\n"
            "Ex: Pedagogia, Ciência da Computação, Direito...",
            parse_mode="Markdown"
        )

    elif state.get('state') == 'aguardando_curso':
        user_state[chat_id]['curso'] = text
        user_state[chat_id]['state'] = 'aguardando_turma'
        await update.message.reply_text(
            f"✅ Curso: *{text}*\n\n"
            "Passo 3/6: Qual a sua *turma*? (Ex: A, B, 2024.1)",
            parse_mode="Markdown"
        )

    elif state.get('state') == 'aguardando_turma':
        user_state[chat_id]['turma'] = text
        user_state[chat_id]['state'] = 'aguardando_semestre'
        await update.message.reply_text(
            f"✅ Turma: *{text}*\n\n"
            "Passo 4/6: Qual o *semestre* atual?\n"
            "Digite um número de 1 a 12 (Ex: 3)",
            parse_mode="Markdown"
        )

    elif state.get('state') == 'aguardando_semestre':
        if not text.isdigit() or int(text) < 1 or int(text) > 12:
            await update.message.reply_text("❌ Digite um número entre 1 e 12.")
            return
        user_state[chat_id]['semestre'] = f"{text}º"
        user_state[chat_id]['state'] = 'aguardando_ra'
        await update.message.reply_text(
            f"✅ Semestre: *{text}º*\n\n"
            "Passo 5/6: Qual seu *RA* (Registro do Aluno)?",
            parse_mode="Markdown"
        )

    elif state.get('state') == 'aguardando_ra':
        user_state[chat_id]['ra'] = text
        user_state[chat_id]['state'] = 'aguardando_senha_cadastro'
        await update.message.reply_text(
            f"✅ RA: *{text}*\n\n"
            "Passo 6/6: Crie uma *senha* para sua conta:",
            parse_mode="Markdown"
        )

    elif state.get('state') == 'aguardando_senha_cadastro':
        dados = user_state[chat_id]
        try:
            conn = get_db()
            conn.execute(
                "INSERT INTO alunos (nome, curso, turma, semestre, ra, telegram_id, senha) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (dados['nome'], dados['curso'], dados['turma'], dados['semestre'], dados['ra'], chat_id, text)
            )
            conn.commit()
            conn.close()
            # Get the actual aluno_id
            aluno = aluno_por_telegram(chat_id)
            if aluno:
                user_state[chat_id]['aluno_id'] = aluno['id']
                user_state[chat_id]['aluno_nome'] = aluno['nome']

            await update.message.reply_text(
                f"✅ *Cadastro realizado com sucesso!*\n\n"
                f"👤 *{dados['nome']}*\n"
                f"📚 {dados['curso']} — Turma {dados['turma']} ({dados['semestre']})\n"
                f"🔢 RA: {dados['ra']}\n\n"
                "Bem-vindo(a)! Agora você pode enviar suas horas complementares 📎",
                parse_mode="Markdown",
                reply_markup=menu_aluno(conectado=True)
            )
        except sqlite3.IntegrityError:
            await update.message.reply_text(
                "❌ Este RA já está cadastrado! Use outro ou faça login.",
                reply_markup=menu_aluno(False)
            )
            user_state.pop(chat_id, None)

    # ── LOGIN ALUNO ───────────────────────────────────────────
    elif state.get('state') == 'aguardando_login_ra':
        user_state[chat_id]['login_ra'] = text
        user_state[chat_id]['state'] = 'aguardando_login_senha'
        await update.message.reply_text("Agora digite sua *senha*:", parse_mode="Markdown")

    elif state.get('state') == 'aguardando_login_senha':
        ra = state.get('login_ra', '')
        aluno = aluno_por_ra_senha(ra, text)
        if aluno:
            # Vincular telegram_id
            conn = get_db()
            conn.execute("UPDATE alunos SET telegram_id = ? WHERE id = ?", (chat_id, aluno['id']))
            conn.commit()
            conn.close()
            user_state[chat_id] = {'tipo': 'aluno', 'aluno_id': aluno['id'], 'aluno_nome': aluno['nome']}
            await update.message.reply_text(
                f"✅ *Login realizado!*\n\n👋 Bem-vindo(a), *{aluno['nome']}*!",
                parse_mode="Markdown",
                reply_markup=menu_aluno(conectado=True)
            )
        else:
            await update.message.reply_text(
                "❌ RA ou senha incorretos! Tente novamente.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔑 Tentar novamente", callback_data="aluno_login")],
                    [InlineKeyboardButton("📝 Cadastrar", callback_data="aluno_cadastro")],
                ])
            )
            user_state.pop(chat_id, None)

    # ── ADMIN LOGIN ───────────────────────────────────────────
    elif state.get('state') == 'aguardando_admin_usuario':
        user_state[chat_id]['admin_user'] = text
        user_state[chat_id]['state'] = 'aguardando_admin_senha'
        await update.message.reply_text("Digite sua *senha de admin*:", parse_mode="Markdown")

    elif state.get('state') == 'aguardando_admin_senha':
        usuario = state.get('admin_user', '')
        admin = admin_por_usuario_senha(usuario, text)
        if admin:
            conn = get_db()
            conn.execute("UPDATE admin SET telegram_id = ? WHERE id = ?", (chat_id, admin['id']))
            conn.commit()
            conn.close()
            user_state[chat_id] = {'tipo': 'admin', 'admin_id': admin['id']}
            await update.message.reply_text(
                f"✅ *Login admin realizado!* Usuário: {usuario}",
                parse_mode="Markdown",
                reply_markup=menu_admin(conectado=True)
            )
        else:
            await update.message.reply_text(
                "❌ Usuário ou senha incorretos!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔑 Tentar novamente", callback_data="admin_login")],
                ])
            )
            user_state.pop(chat_id, None)

    # ── NOVA SUBMISSÃO ────────────────────────────────────────
    elif state.get('state') == 'aguardando_evento':
        user_state[chat_id]['evento'] = text
        user_state[chat_id]['state'] = 'aguardando_horas'
        await update.message.reply_text(
            f"✅ Evento: *{text}*\n\n"
            "Passo 2/3: Quantas *horas* dura essa atividade?\n"
            "(Ex: 4, 2.5, 8)",
            parse_mode="Markdown"
        )

    elif state.get('state') == 'aguardando_horas':
        try:
            horas = float(text.replace(',', '.'))
            if horas <= 0 or horas > 999:
                raise ValueError
            user_state[chat_id]['horas'] = horas
            user_state[chat_id]['state'] = 'aguardando_comprovante'
            await update.message.reply_text(
                f"✅ Horas: *{horas}h*\n\n"
                "Passo 3/3: Envie o *comprovante* 📎\n"
                "Pode ser uma *foto* ou *PDF* do certificado/declaração.",
                parse_mode="Markdown"
            )
        except ValueError:
            await update.message.reply_text(
                "❌ Digite um número válido (Ex: 4, 2.5, 8).",
            )

    # ── MOTIVO REJEIÇÃO ───────────────────────────────────────
    elif state.get('state') == 'aguardando_motivo_rejeicao':
        sub_id = state.get('sub_id')
        conn = get_db()
        conn.execute(
            "UPDATE submissao SET status = 'rejeitado', admin_nota = ?, data_revisao = ? WHERE id = ?",
            (text, datetime.now().isoformat(), sub_id)
        )
        conn.commit()
        conn.close()
        user_state[chat_id] = {'tipo': 'admin'}
        await update.message.reply_text(
            f"❌ *Submissão #{sub_id} rejeitada.*\nMotivo: {text}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 Ver pendentes", callback_data="admin_pendentes")],
                [InlineKeyboardButton("🔙 Menu Admin", callback_data="menu_admin")]
            ])
        )

    else:
        await update.message.reply_text(
            "Use /start para ver o menu principal.",
            reply_markup=menu_principal()
        )

# ─── RECEBER FOTOS / DOCUMENTOS ─────────────────────────────────
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    state = user_state.get(chat_id, {})

    if state.get('state') != 'aguardando_comprovante':
        await update.message.reply_text(
            "❌ Não esperava um arquivo agora.\n"
            "Use /start e vá em *Nova Submissão* para começar.",
            parse_mode="Markdown",
            reply_markup=menu_aluno(conectado=True)
        )
        return

    # Baixar o arquivo
    try:
        if update.message.photo:
            # É uma foto - pegar a de maior resolução
            file_id = update.message.photo[-1].file_id
            ext = "jpg"
        elif update.message.document:
            file_id = update.message.document.file_id
            nome_original = update.message.document.file_name or "comprovante"
            ext = nome_original.rsplit('.', 1)[-1].lower() if '.' in nome_original else "pdf"
        else:
            await update.message.reply_text("❌ Formato não suportado. Envie foto ou PDF.")
            return

        file = await context.bot.get_file(file_id)
        nome_arquivo = f"{uuid.uuid4().hex}.{ext}"
        filepath = os.path.join(UPLOAD_DIR, nome_arquivo)
        await file.download_to_drive(filepath)

        # Salvar no banco
        conn = get_db()
        conn.execute(
            "INSERT INTO submissao (aluno_id, evento, descricao, horas, arquivo) VALUES (?, ?, ?, ?, ?)",
            (state['aluno_id'], state['evento'], '', state['horas'], nome_arquivo)
        )
        conn.commit()
        conn.close()

        user_state[chat_id] = {'tipo': 'aluno', 'aluno_id': state['aluno_id'], 'aluno_nome': state.get('aluno_nome', '')}

        await update.message.reply_text(
            f"✅ *Submissão enviada com sucesso!*\n\n"
            f"📌 *{state['evento']}*\n"
            f"⏱️ {state['horas']}h\n"
            f"📎 Comprovante anexado\n\n"
            "Agora é só aguardar a aprovação do admin ⏳",
            parse_mode="Markdown",
            reply_markup=menu_aluno(conectado=True)
        )

        # Notificar admin se tiver telegram_id cadastrado
        conn = get_db()
        admins = conn.execute("SELECT telegram_id FROM admin WHERE telegram_id IS NOT NULL").fetchall()
        conn.close()
        for adm in admins:
            try:
                aluno = aluno_por_telegram(chat_id)
                await context.bot.send_message(
                    chat_id=adm['telegram_id'],
                    text=f"📢 *Nova submissão pendente!*\n\n"
                         f"👤 {aluno['nome'] if aluno else 'Aluno'} — {state['evento']}\n"
                         f"⏱️ {state['horas']}h\n"
                         f"📅 Comprovante anexado.",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📋 Ver pendentes", callback_data="admin_pendentes")],
                    ])
                )
            except:
                pass

    except Exception as e:
        logger.error(f"Erro ao processar arquivo: {e}")
        await update.message.reply_text(f"❌ Erro ao salvar o comprovante: {str(e)}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_state[chat_id] = {}
    await update.message.reply_text("❌ Operação cancelada.", reply_markup=menu_principal())

# ─── MAIN ───────────────────────────────────────────────────────
def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancelar", cancel))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, file_handler))

    logger.info("Bot rodando...")
    app.run_polling()

if __name__ == "__main__":
    main()

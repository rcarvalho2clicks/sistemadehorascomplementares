import os
import uuid
from datetime import datetime
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory

# Database
import sqlite3
try:
    import psycopg2
    import psycopg2.extras
    HAS_POSTGRES = True
except ImportError:
    HAS_POSTGRES = False

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'horas-complementares-secret-key-mude-isso')
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5MB
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'gif', 'webp'}

# Database configuration
DATABASE_URL = os.environ.get('DATABASE_URL')

if DATABASE_URL and HAS_POSTGRES:
    # PostgreSQL (Render, produção)
    def get_db():
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return conn
else:
    # SQLite (desenvolvimento local)
    PERSISTENT_DIR = os.environ.get('PERSISTENT_DIR', os.path.join(app.root_path, 'static', 'uploads', '..'))
    DATABASE = os.path.join(PERSISTENT_DIR, 'database.db')
    os.makedirs(PERSISTENT_DIR, exist_ok=True)
    
    def get_db():
        conn = sqlite3.connect(DATABASE)
        conn.row_factory = sqlite3.Row
        return conn

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def is_postgres():
    return bool(DATABASE_URL and HAS_POSTGRES)

# Detecta tipo de banco ativo
_IS_POSTGRES = is_postgres()

class ResultRow:
    """Wrapper que suporta acesso por nome ['coluna'] e por índice [0],
    compatível com sqlite3.Row para migração transparente."""
    __slots__ = ('_data', '_keys')
    
    def __init__(self, keys, values):
        self._keys = keys
        self._data = values
    
    def __getitem__(self, key):
        if isinstance(key, (int,)):
            return self._data[key]
        return self._data[self._keys.index(key)]
    
    def __bool__(self):
        return True
    
    def get(self, key, default=None):
        try:
            return self[key]
        except (IndexError, ValueError):
            return default

def execute_query(query, params=(), fetch=False, fetchone=False, commit=False):
    """Executa query com placeholder correto para cada banco."""
    conn = get_db()
    cur = conn.cursor()
    
    if _IS_POSTGRES:
        query = query.replace('?', '%s')
    
    cur.execute(query, params)
    cols = [desc[0] for desc in cur.description] if cur.description else []
    
    if commit:
        conn.commit()
    
    if fetchone:
        row = cur.fetchone()
        result = ResultRow(cols, row) if row is not None else None
        cur.close()
        conn.close()
        return result
    elif fetch:
        rows = cur.fetchall()
        result = [ResultRow(cols, r) for r in rows]
        cur.close()
        conn.close()
        return result
    
    cur.close()
    conn.close()
    return None

def init_db():
    conn = get_db()
    cur = conn.cursor()
    
    if is_postgres():
        # PostgreSQL schema
        cur.execute('''
            CREATE TABLE IF NOT EXISTS alunos (
                id SERIAL PRIMARY KEY,
                nome TEXT NOT NULL,
                curso TEXT NOT NULL,
                turma TEXT NOT NULL,
                semestre TEXT NOT NULL,
                ra TEXT NOT NULL UNIQUE,
                email TEXT,
                senha TEXT NOT NULL,
                data_cadastro TIMESTAMP DEFAULT NOW()
            );
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS admin (
                id SERIAL PRIMARY KEY,
                usuario TEXT NOT NULL UNIQUE,
                senha TEXT NOT NULL
            );
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS submissao (
                id SERIAL PRIMARY KEY,
                aluno_id INTEGER NOT NULL REFERENCES alunos(id),
                evento TEXT NOT NULL,
                descricao TEXT,
                horas REAL NOT NULL,
                arquivo TEXT NOT NULL,
                status TEXT DEFAULT 'pendente',
                admin_nota TEXT,
                data_envio TIMESTAMP DEFAULT NOW(),
                data_revisao TIMESTAMP
            );
        ''')
        
        # Admin padrão
        cur.execute("SELECT id FROM admin WHERE usuario = %s", ('admin',))
        if not cur.fetchone():
            cur.execute("INSERT INTO admin (usuario, senha) VALUES (%s, %s)", ('admin', 'admin123'))
    else:
        # SQLite schema
        cur.executescript('''
            CREATE TABLE IF NOT EXISTS alunos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                curso TEXT NOT NULL,
                turma TEXT NOT NULL,
                semestre TEXT NOT NULL,
                ra TEXT NOT NULL UNIQUE,
                email TEXT,
                senha TEXT NOT NULL,
                data_cadastro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS admin (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario TEXT NOT NULL UNIQUE,
                senha TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS submissao (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                aluno_id INTEGER NOT NULL,
                evento TEXT NOT NULL,
                descricao TEXT,
                horas REAL NOT NULL,
                arquivo TEXT NOT NULL,
                status TEXT DEFAULT 'pendente',
                admin_nota TEXT,
                data_envio TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                data_revisao TIMESTAMP,
                FOREIGN KEY (aluno_id) REFERENCES alunos(id)
            );
        ''')
        
        # Admin padrão
        cur.execute("SELECT id FROM admin WHERE usuario = ?", ('admin',))
        if not cur.fetchone():
            cur.execute("INSERT INTO admin (usuario, senha) VALUES (?, ?)", ('admin', 'admin123'))
    
    conn.commit()
    cur.close()
    conn.close()

# Inicializa banco de dados na carga do módulo (gunicorn)
init_db()

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ─── ROTAS PÚBLICAS ───────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/aluno/cadastro', methods=['GET', 'POST'])
def aluno_cadastro():
    if request.method == 'POST':
        nome = request.form['nome']
        curso = request.form['curso']
        turma = request.form['turma']
        semestre = request.form['semestre']
        ra = request.form['ra']
        email = request.form['email']
        senha = request.form['senha']

        try:
            execute_query(
                "INSERT INTO alunos (nome, curso, turma, semestre, ra, email, senha) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (nome, curso, turma, semestre, ra, email, senha),
                commit=True
            )
            flash('Cadastro realizado com sucesso! Faça login.', 'success')
        except (sqlite3.IntegrityError, Exception) as e:
            # PostgreSQL raises psycopg2.errors.UniqueViolation
            if 'unique' in str(e).lower() or 'duplicate' in str(e).lower():
                flash('RA já cadastrado!', 'danger')
            else:
                raise
            return render_template('aluno_cadastro.html')
        return redirect(url_for('aluno_login'))
    return render_template('aluno_cadastro.html')

@app.route('/aluno/login', methods=['GET', 'POST'])
def aluno_login():
    if request.method == 'POST':
        ra = request.form['ra']
        senha = request.form['senha']
        aluno = execute_query(
            "SELECT * FROM alunos WHERE ra = ? AND senha = ?", 
            (ra, senha), 
            fetchone=True
        )
        if aluno:
            session['aluno_id'] = aluno['id']
            session['aluno_nome'] = aluno['nome']
            return redirect(url_for('aluno_dashboard'))
        flash('RA ou senha incorretos!', 'danger')
    return render_template('aluno_login.html')

@app.route('/aluno/logout')
def aluno_logout():
    session.pop('aluno_id', None)
    session.pop('aluno_nome', None)
    return redirect(url_for('index'))

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        usuario = request.form['usuario']
        senha = request.form['senha']
        admin = execute_query(
            "SELECT * FROM admin WHERE usuario = ? AND senha = ?", 
            (usuario, senha), 
            fetchone=True
        )
        if admin:
            session['admin_id'] = admin['id']
            session['admin_user'] = admin['usuario']
            return redirect(url_for('admin_dashboard'))
        flash('Usuário ou senha incorretos!', 'danger')
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_id', None)
    session.pop('admin_user', None)
    return redirect(url_for('index'))

# ─── ROTAS DO ALUNO ─────────────────────────────────────────────

@app.route('/aluno/dashboard')
def aluno_dashboard():
    if 'aluno_id' not in session:
        return redirect(url_for('aluno_login'))

    aluno = execute_query(
        "SELECT * FROM alunos WHERE id = ?", 
        (session['aluno_id'],), 
        fetchone=True
    )
    submissoes = execute_query(
        "SELECT * FROM submissao WHERE aluno_id = ? ORDER BY data_envio DESC",
        (session['aluno_id'],),
        fetch=True
    )

    # Estatísticas
    total_horas = execute_query(
        "SELECT COALESCE(SUM(horas), 0) FROM submissao WHERE aluno_id = ? AND status = 'aprovado'",
        (session['aluno_id'],),
        fetchone=True
    )[0]
    pendentes = execute_query(
        "SELECT COUNT(*) FROM submissao WHERE aluno_id = ? AND status = 'pendente'",
        (session['aluno_id'],),
        fetchone=True
    )[0]

    return render_template('aluno_dashboard.html',
                         aluno=aluno,
                         submissoes=submissoes,
                         total_horas=total_horas,
                         pendentes=pendentes)

@app.route('/aluno/nova', methods=['GET', 'POST'])
def aluno_nova_submissao():
    if 'aluno_id' not in session:
        return redirect(url_for('aluno_login'))

    if request.method == 'POST':
        evento = request.form['evento']
        descricao = request.form['descricao']
        horas_str = request.form['horas']
        arquivo = request.files['arquivo']

        # Converte vírgula brasileira para ponto decimal
        horas_str = horas_str.replace(',', '.')

        if not arquivo or not allowed_file(arquivo.filename):
            flash('Envie um arquivo válido (PDF, PNG, JPG, GIF)', 'danger')
            return render_template('aluno_nova.html')

        try:
            horas = float(horas_str)
        except ValueError:
            flash('Carga horária inválida! Use números (ex: 4,5 ou 4.5)', 'danger')
            return render_template('aluno_nova.html')

        # Salva arquivo com nome único
        ext = arquivo.filename.rsplit('.', 1)[1].lower()
        nome_arquivo = f"{uuid.uuid4().hex}.{ext}"
        arquivo.save(os.path.join(app.config['UPLOAD_FOLDER'], nome_arquivo))

        execute_query(
            "INSERT INTO submissao (aluno_id, evento, descricao, horas, arquivo) VALUES (?, ?, ?, ?, ?)",
            (session['aluno_id'], evento, descricao, float(horas), nome_arquivo),
            commit=True
        )

        flash('Horas complementares enviadas para aprovação!', 'success')
        return redirect(url_for('aluno_dashboard'))

    return render_template('aluno_nova.html')

@app.route('/aluno/submissao/<int:id>')
def aluno_ver_submissao(id):
    if 'aluno_id' not in session:
        return redirect(url_for('aluno_login'))
    sub = execute_query(
        "SELECT * FROM submissao WHERE id = ? AND aluno_id = ?",
        (id, session['aluno_id']),
        fetchone=True
    )
    if not sub:
        flash('Submissão não encontrada!', 'danger')
        return redirect(url_for('aluno_dashboard'))
    return render_template('aluno_submissao.html', sub=sub)

# ─── ROTAS DO ADMIN ──────────────────────────────────────────────

@app.route('/admin/dashboard')
def admin_dashboard():
    if 'admin_id' not in session:
        return redirect(url_for('admin_login'))

    pendentes = execute_query('''
        SELECT s.*, a.nome, a.curso, a.turma, a.semestre, a.ra
        FROM submissao s
        JOIN alunos a ON s.aluno_id = a.id
        WHERE s.status = 'pendente'
        ORDER BY s.data_envio DESC
    ''', fetch=True)

    historico = execute_query('''
        SELECT s.*, a.nome, a.curso, a.turma, a.semestre, a.ra
        FROM submissao s
        JOIN alunos a ON s.aluno_id = a.id
        WHERE s.status != 'pendente'
        ORDER BY s.data_revisao DESC
        LIMIT 50
    ''', fetch=True)

    stats = execute_query('''
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status='pendente' THEN 1 ELSE 0 END) as pendentes,
            SUM(CASE WHEN status='aprovado' THEN 1 ELSE 0 END) as aprovados,
            SUM(CASE WHEN status='rejeitado' THEN 1 ELSE 0 END) as rejeitados
        FROM submissao
    ''', fetchone=True)

    return render_template('admin_dashboard.html',
                         pendentes=pendentes,
                         historico=historico,
                         stats=stats)

@app.route('/admin/aprovar/<int:id>', methods=['POST'])
def admin_aprovar(id):
    if 'admin_id' not in session:
        return redirect(url_for('admin_login'))
    nota = request.form.get('nota', '')
    execute_query(
        "UPDATE submissao SET status = 'aprovado', admin_nota = ?, data_revisao = ? WHERE id = ?",
        (nota, datetime.now().isoformat(), id),
        commit=True
    )
    flash('Submissão aprovada!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/rejeitar/<int:id>', methods=['POST'])
def admin_rejeitar(id):
    if 'admin_id' not in session:
        return redirect(url_for('admin_login'))
    nota = request.form.get('nota', '')
    execute_query(
        "UPDATE submissao SET status = 'rejeitado', admin_nota = ?, data_revisao = ? WHERE id = ?",
        (nota, datetime.now().isoformat(), id),
        commit=True
    )
    flash('Submissão rejeitada!', 'warning')
    return redirect(url_for('admin_dashboard'))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
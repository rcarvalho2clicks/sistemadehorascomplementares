import os
import sqlite3
import uuid
from datetime import datetime
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'horas-complementares-secret-key-mude-isso')
app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5MB
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'gif', 'webp'}
DATABASE = os.path.join(app.root_path, 'database.db')

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

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

    # Admin padrão (se não existir)
    admin_exists = conn.execute("SELECT id FROM admin WHERE usuario = 'admin'").fetchone()
    if not admin_exists:
        conn.execute("INSERT INTO admin (usuario, senha) VALUES (?, ?)",
                     ('admin', 'admin123'))
    conn.commit()
    conn.close()

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

        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO alunos (nome, curso, turma, semestre, ra, email, senha) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (nome, curso, turma, semestre, ra, email, senha)
            )
            conn.commit()
            flash('Cadastro realizado com sucesso! Faça login.', 'success')
        except sqlite3.IntegrityError:
            flash('RA já cadastrado!', 'danger')
            return render_template('aluno_cadastro.html')
        finally:
            conn.close()
        return redirect(url_for('aluno_login'))
    return render_template('aluno_cadastro.html')

@app.route('/aluno/login', methods=['GET', 'POST'])
def aluno_login():
    if request.method == 'POST':
        ra = request.form['ra']
        senha = request.form['senha']
        conn = get_db()
        aluno = conn.execute("SELECT * FROM alunos WHERE ra = ? AND senha = ?", (ra, senha)).fetchone()
        conn.close()
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
        conn = get_db()
        admin = conn.execute("SELECT * FROM admin WHERE usuario = ? AND senha = ?", (usuario, senha)).fetchone()
        conn.close()
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

    conn = get_db()
    aluno = conn.execute("SELECT * FROM alunos WHERE id = ?", (session['aluno_id'],)).fetchone()
    submissoes = conn.execute(
        "SELECT * FROM submissao WHERE aluno_id = ? ORDER BY data_envio DESC",
        (session['aluno_id'],)
    ).fetchall()

    # Estatísticas
    total_horas = conn.execute(
        "SELECT COALESCE(SUM(horas), 0) FROM submissao WHERE aluno_id = ? AND status = 'aprovado'",
        (session['aluno_id'],)
    ).fetchone()[0]
    pendentes = conn.execute(
        "SELECT COUNT(*) FROM submissao WHERE aluno_id = ? AND status = 'pendente'",
        (session['aluno_id'],)
    ).fetchone()[0]
    conn.close()

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
        horas = request.form['horas']
        arquivo = request.files['arquivo']

        if not arquivo or not allowed_file(arquivo.filename):
            flash('Envie um arquivo válido (PDF, PNG, JPG, GIF)', 'danger')
            return render_template('aluno_nova.html')

        # Salva arquivo com nome único
        ext = arquivo.filename.rsplit('.', 1)[1].lower()
        nome_arquivo = f"{uuid.uuid4().hex}.{ext}"
        arquivo.save(os.path.join(app.config['UPLOAD_FOLDER'], nome_arquivo))

        conn = get_db()
        conn.execute(
            "INSERT INTO submissao (aluno_id, evento, descricao, horas, arquivo) VALUES (?, ?, ?, ?, ?)",
            (session['aluno_id'], evento, descricao, float(horas), nome_arquivo)
        )
        conn.commit()
        conn.close()

        flash('Horas complementares enviadas para aprovação!', 'success')
        return redirect(url_for('aluno_dashboard'))

    return render_template('aluno_nova.html')

@app.route('/aluno/submissao/<int:id>')
def aluno_ver_submissao(id):
    if 'aluno_id' not in session:
        return redirect(url_for('aluno_login'))
    conn = get_db()
    sub = conn.execute("SELECT * FROM submissao WHERE id = ? AND aluno_id = ?",
                      (id, session['aluno_id'])).fetchone()
    conn.close()
    if not sub:
        flash('Submissão não encontrada!', 'danger')
        return redirect(url_for('aluno_dashboard'))
    return render_template('aluno_submissao.html', sub=sub)

# ─── ROTAS DO ADMIN ──────────────────────────────────────────────

@app.route('/admin/dashboard')
def admin_dashboard():
    if 'admin_id' not in session:
        return redirect(url_for('admin_login'))

    conn = get_db()
    pendentes = conn.execute('''
        SELECT s.*, a.nome, a.curso, a.turma, a.semestre, a.ra
        FROM submissao s
        JOIN alunos a ON s.aluno_id = a.id
        WHERE s.status = 'pendente'
        ORDER BY s.data_envio DESC
    ''').fetchall()

    historico = conn.execute('''
        SELECT s.*, a.nome, a.curso, a.turma, a.semestre, a.ra
        FROM submissao s
        JOIN alunos a ON s.aluno_id = a.id
        WHERE s.status != 'pendente'
        ORDER BY s.data_revisao DESC
        LIMIT 50
    ''').fetchall()

    stats = conn.execute('''
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN status='pendente' THEN 1 ELSE 0 END) as pendentes,
            SUM(CASE WHEN status='aprovado' THEN 1 ELSE 0 END) as aprovados,
            SUM(CASE WHEN status='rejeitado' THEN 1 ELSE 0 END) as rejeitados
        FROM submissao
    ''').fetchone()

    conn.close()

    return render_template('admin_dashboard.html',
                         pendentes=pendentes,
                         historico=historico,
                         stats=stats)

@app.route('/admin/aprovar/<int:id>', methods=['POST'])
def admin_aprovar(id):
    if 'admin_id' not in session:
        return redirect(url_for('admin_login'))
    nota = request.form.get('nota', '')
    conn = get_db()
    conn.execute(
        "UPDATE submissao SET status = 'aprovado', admin_nota = ?, data_revisao = ? WHERE id = ?",
        (nota, datetime.now().isoformat(), id)
    )
    conn.commit()
    conn.close()
    flash('Submissão aprovada!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/rejeitar/<int:id>', methods=['POST'])
def admin_rejeitar(id):
    if 'admin_id' not in session:
        return redirect(url_for('admin_login'))
    nota = request.form.get('nota', '')
    conn = get_db()
    conn.execute(
        "UPDATE submissao SET status = 'rejeitado', admin_nota = ?, data_revisao = ? WHERE id = ?",
        (nota, datetime.now().isoformat(), id)
    )
    conn.commit()
    conn.close()
    flash('Submissão rejeitada!', 'warning')
    return redirect(url_for('admin_dashboard'))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

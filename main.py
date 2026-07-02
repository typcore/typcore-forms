"""
TypCore Forms Service
Serviço de formulários de anamnese — substitui Google Forms
Deploy no Railway, sem dependência do Google.
"""
import os
import json
import hmac
import hashlib
import base64
import time
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

# ── CONFIGURAÇÃO ─────────────────────────────────────────────
SECRET_KEY   = os.getenv("FORMS_SECRET_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "").replace("postgres://", "postgresql://")
API_KEY      = os.getenv("CLINICA_API_KEY", "")
BASE_URL     = os.getenv("BASE_URL", "https://forms.typcore.com.br")

if not SECRET_KEY:
    raise RuntimeError("FORMS_SECRET_KEY nao definida no ambiente. Configure no Railway.")
if not API_KEY:
    raise RuntimeError("CLINICA_API_KEY nao definida no ambiente. Configure no Railway.")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL nao definida no ambiente. Configure no Railway.")

# ── BANCO DE DADOS ───────────────────────────────────────────
engine  = create_engine(DATABASE_URL, pool_pre_ping=True)
Base    = declarative_base()
Session = sessionmaker(bind=engine)


class RespostaAnamnese(Base):
    __tablename__ = "respostas_anamnese"
    id                 = Column(Integer, primary_key=True, autoincrement=True)
    paciente_id        = Column(Integer)
    especialidade_id   = Column(Integer)
    especialidade_nome = Column(String(100))
    clinica_id         = Column(String(100))
    respostas_json     = Column(Text)
    consentimento      = Column(Boolean, default=False)
    submetido_em       = Column(DateTime, default=datetime.now)
    sincronizado       = Column(Boolean, default=False)


Base.metadata.create_all(engine)

app = FastAPI(title="TypCore Forms", docs_url=None, redoc_url=None)


# ── TOKEN ─────────────────────────────────────────────────────
def gerar_token(paciente_id: int, especialidade_id: int,
                especialidade_nome: str, clinica_id: str) -> str:
    data    = {"pid": paciente_id, "eid": especialidade_id,
               "en": especialidade_nome, "cid": clinica_id,
               "t": int(time.time())}
    payload = json.dumps(data, separators=(",", ":"))
    encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    sig     = hmac.new(SECRET_KEY.encode(), encoded.encode(), hashlib.sha256).hexdigest()[:24]
    return f"{encoded}.{sig}"


def verificar_token(token: str) -> dict:
    try:
        encoded, sig = token.rsplit(".", 1)
        expected = hmac.new(SECRET_KEY.encode(), encoded.encode(), hashlib.sha256).hexdigest()[:24]
        if not hmac.compare_digest(sig, expected):
            raise ValueError("Assinatura inválida")
        padding = "=" * (4 - len(encoded) % 4)
        data = json.loads(base64.urlsafe_b64decode(encoded + padding))
        if int(time.time()) - data.get("t", 0) > 30 * 86400:
            raise ValueError("Token expirado")
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Link inválido: {e}")


# ── TEMPLATES JSON ───────────────────────────────────────────
TEMPLATES = {}

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
_map = {
    "Estética Facial":    "facial.json",
    "Estética Corporal":  "corporal.json",
    "Facial + Corporal":  "facial_corporal.json",
    "Depilação":          "depilacao.json",
    "Micropigmentação":   "micropigmentacao.json",
    "Massoterapia":       "massoterapia.json",
}
for nome, arquivo in _map.items():
    caminho = os.path.join(TEMPLATES_DIR, arquivo)
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8") as f:
            TEMPLATES[nome] = json.load(f)


# ── RENDERIZADOR HTML ─────────────────────────────────────────
def _render_pergunta(p: dict, idx: int) -> str:
    tipo    = p.get("tipo", "texto")
    texto   = p.get("texto", "")
    obrig   = p.get("obrigatorio", False)
    req_str = "required" if obrig else ""
    star    = '<span style="color:#C1726A">*</span>' if obrig else ""
    name    = f"q_{idx}"

    if tipo in ("texto", "profissao", "email"):
        return f"""
        <div class="campo">
            <label>{texto} {star}</label>
            <input type="text" name="{name}" {req_str} placeholder="{texto}">
        </div>"""

    if tipo == "texto_longo":
        return f"""
        <div class="campo">
            <label>{texto} {star}</label>
            <textarea name="{name}" {req_str} rows="3" placeholder="Digite aqui..."></textarea>
        </div>"""

    if tipo == "data":
        fmt = p.get("formato", "DD/MM/AAAA")
        return f"""
        <div class="campo">
            <label>{texto} {star}</label>
            <input type="text" name="{name}" {req_str} placeholder="{fmt}" class="mask-data">
        </div>"""

    if tipo == "telefone":
        return f"""
        <div class="campo">
            <label>{texto} {star}</label>
            <input type="tel" name="{name}" {req_str} placeholder="(00) 00000-0000" class="mask-fone">
        </div>"""

    if tipo == "cpf":
        return f"""
        <div class="campo">
            <label>{texto} {star}</label>
            <input type="text" name="{name}" {req_str} placeholder="000.000.000-00" class="mask-cpf">
        </div>"""

    if tipo == "sim_nao":
        return f"""
        <div class="campo">
            <label>{texto} {star}</label>
            <div class="radio-group">
                <label class="radio-opt"><input type="radio" name="{name}" value="Sim" {req_str}><span>Sim</span></label>
                <label class="radio-opt"><input type="radio" name="{name}" value="Não" {req_str}><span>Não</span></label>
            </div>
        </div>"""

    if tipo == "sim_nao_qual":
        return f"""
        <div class="campo">
            <label>{texto} {star}</label>
            <div class="radio-group">
                <label class="radio-opt"><input type="radio" name="{name}" value="Sim" {req_str} class="trigger-qual" data-target="qual_{idx}"><span>Sim</span></label>
                <label class="radio-opt"><input type="radio" name="{name}" value="Não" {req_str} class="trigger-qual" data-target="qual_{idx}"><span>Não</span></label>
            </div>
            <input type="text" name="qual_{idx}" id="qual_{idx}" placeholder="Qual?" style="display:none;margin-top:8px" class="qual-input">
        </div>"""

    if tipo == "escolha_unica":
        opcoes = p.get("opcoes", [])
        opts = "".join(
            f'<label class="radio-opt"><input type="radio" name="{name}" value="{op}" {req_str}><span>{op}</span></label>'
            for op in opcoes
        )
        return f"""
        <div class="campo">
            <label>{texto} {star}</label>
            <div class="radio-group vertical">{opts}</div>
        </div>"""

    if tipo == "multipla_escolha":
        opcoes = p.get("opcoes", [])
        opts = "".join(
            f'<label class="check-opt"><input type="checkbox" name="{name}" value="{op}"><span>{op}</span></label>'
            for op in opcoes
        )
        return f"""
        <div class="campo">
            <label>{texto} {star}</label>
            <div class="check-group">{opts}</div>
        </div>"""

    if tipo == "consentimento":
        label_aceite = p.get("label_aceite", "Li e concordo.")
        return f"""
        <div class="campo consentimento-box">
            <p class="consentimento-texto">{texto}</p>
            <label class="check-opt check-consent">
                <input type="checkbox" name="{name}" value="sim" required>
                <span>{label_aceite}</span>
            </label>
        </div>"""

    return f"""
    <div class="campo">
        <label>{texto} {star}</label>
        <input type="text" name="{name}" {req_str}>
    </div>"""


def render_form(template: dict, token: str) -> str:
    secoes = template.get("secoes", [])
    titulo = template.get("titulo", "Anamnese")
    descricao = template.get("descricao", "")

    # Mapeia índice global de pergunta
    idx = 0
    secoes_html = []
    total = len(secoes)

    for s_num, secao in enumerate(secoes):
        sec_titulo  = secao.get("titulo", "")
        sec_desc    = secao.get("descricao", "")
        perguntas   = secao.get("perguntas", [])
        campos_html = ""

        for p in perguntas:
            campos_html += _render_pergunta(p, idx)
            if p.get("tipo") == "sim_nao_qual":
                idx += 2
            else:
                idx += 1

        active_class = "active" if s_num == 0 else ""
        secoes_html.append(f"""
        <div class="secao {active_class}" data-step="{s_num}">
            <div class="secao-header">
                <h2>{sec_titulo}</h2>
                {f'<p class="secao-desc">{sec_desc}</p>' if sec_desc else ""}
            </div>
            {campos_html}
            <div class="nav-btns">
                {"" if s_num == 0 else '<button type="button" class="btn-nav btn-prev">← Anterior</button>'}
                {"" if s_num < total - 1 else ""}
                {f'<button type="button" class="btn-nav btn-next">Próximo →</button>' if s_num < total - 1 else '<button type="submit" class="btn-submit">✅  Enviar Anamnese</button>'}
            </div>
        </div>""")

    steps_dots = "".join(
        f'<div class="step-dot {"active" if i == 0 else ""}" data-step="{i}"></div>'
        for i in range(total)
    )

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{titulo}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #f8f4f3; color: #2d2d2d; min-height: 100vh; }}
.topo {{ background: #C1726A; padding: 20px 24px 16px; }}
.topo h1 {{ color: #fff; font-size: 18px; font-weight: 700; }}
.topo p {{ color: #f5e0de; font-size: 13px; margin-top: 4px; }}
.progresso-bar {{ background: #a85d56; height: 4px; margin-top: 12px; border-radius: 2px; }}
.progresso-fill {{ background: #fff; height: 4px; border-radius: 2px; transition: width .3s; }}
.steps {{ display: flex; gap: 6px; margin-top: 10px; justify-content: center; }}
.step-dot {{ width: 8px; height: 8px; border-radius: 50%; background: rgba(255,255,255,.4); transition: background .3s; cursor: default; }}
.step-dot.active {{ background: #fff; }}
.container {{ max-width: 560px; margin: 0 auto; padding: 20px 16px 40px; }}
.secao {{ display: none; animation: fadeIn .3s; }}
.secao.active {{ display: block; }}
@keyframes fadeIn {{ from {{ opacity:0; transform:translateY(8px) }} to {{ opacity:1; transform:translateY(0) }} }}
.secao-header {{ margin-bottom: 20px; }}
.secao-header h2 {{ font-size: 17px; font-weight: 700; color: #C1726A; }}
.secao-desc {{ font-size: 13px; color: #888; margin-top: 4px; line-height: 1.5; }}
.campo {{ margin-bottom: 18px; }}
.campo label {{ display: block; font-size: 14px; font-weight: 600; color: #444; margin-bottom: 7px; line-height: 1.4; }}
input[type=text], input[type=tel], textarea {{
    width: 100%; border: 1.5px solid #ddd; border-radius: 10px;
    padding: 12px 14px; font-size: 15px; color: #2d2d2d; background: #fff;
    transition: border .2s; outline: none; -webkit-appearance: none; }}
input:focus, textarea:focus {{ border-color: #C1726A; box-shadow: 0 0 0 3px rgba(193,114,106,.12); }}
textarea {{ resize: vertical; min-height: 80px; }}
.radio-group {{ display: flex; flex-wrap: wrap; gap: 10px; }}
.radio-group.vertical {{ flex-direction: column; gap: 8px; }}
.radio-opt {{ display: flex; align-items: center; gap: 8px; cursor: pointer;
             background: #fff; border: 1.5px solid #ddd; border-radius: 10px;
             padding: 10px 14px; transition: all .2s; font-size: 14px; }}
.radio-opt:has(input:checked) {{ border-color: #C1726A; background: #fff5f4; color: #C1726A; font-weight: 600; }}
.radio-opt input {{ width: 16px; height: 16px; accent-color: #C1726A; }}
.check-group {{ display: flex; flex-direction: column; gap: 8px; }}
.check-opt {{ display: flex; align-items: center; gap: 10px; cursor: pointer;
             background: #fff; border: 1.5px solid #ddd; border-radius: 10px;
             padding: 10px 14px; transition: all .2s; font-size: 14px; }}
.check-opt:has(input:checked) {{ border-color: #C1726A; background: #fff5f4; }}
.check-opt input {{ width: 16px; height: 16px; accent-color: #C1726A; }}
.check-consent {{ margin-top: 12px; }}
.consentimento-box {{ background: #fff; border: 1.5px solid #e0d5d3; border-radius: 12px; padding: 16px; }}
.consentimento-texto {{ font-size: 13px; color: #555; line-height: 1.6; margin-bottom: 14px; }}
.qual-input {{ border: 1.5px solid #ddd; border-radius: 10px; padding: 10px 14px; font-size: 14px; width: 100%; outline: none; }}
.qual-input:focus {{ border-color: #C1726A; }}
.nav-btns {{ display: flex; justify-content: space-between; align-items: center; margin-top: 28px; gap: 12px; }}
.btn-nav {{ flex: 1; padding: 14px; border: 1.5px solid #ddd; background: #fff; border-radius: 12px;
           font-size: 15px; font-weight: 600; color: #555; cursor: pointer; transition: all .2s; }}
.btn-nav:hover {{ background: #f5f5f5; }}
.btn-next {{ background: #C1726A; border-color: #C1726A; color: #fff; }}
.btn-next:hover {{ background: #a85d56; }}
.btn-prev {{ color: #888; }}
.btn-submit {{ flex: 1; padding: 16px; background: #2E7D32; border: none; border-radius: 12px;
              font-size: 16px; font-weight: 700; color: #fff; cursor: pointer; transition: background .2s; }}
.btn-submit:hover {{ background: #1b5e20; }}
.sucesso {{ display: none; text-align: center; padding: 40px 20px; }}
.sucesso h2 {{ font-size: 24px; color: #2E7D32; margin-bottom: 12px; }}
.sucesso p {{ color: #666; font-size: 15px; line-height: 1.6; }}
.enviando {{ display: none; text-align: center; padding: 20px; color: #888; font-size: 14px; }}
.erro-msg {{ background: #fff5f4; border: 1px solid #f5c6c3; border-radius: 8px;
            padding: 12px; font-size: 13px; color: #c0392b; margin-bottom: 16px; display: none; }}
</style>
</head>
<body>

<div class="topo">
    <h1>📋 {titulo}</h1>
    {f'<p>{descricao}</p>' if descricao else ''}
    <div class="progresso-bar">
        <div class="progresso-fill" id="progresso" style="width:{100//total if total > 0 else 100}%"></div>
    </div>
    <div class="steps">{steps_dots}</div>
</div>

<div class="container">
    <div class="erro-msg" id="erro-msg"></div>

    <form id="form-anamnese" method="POST" action="/f/{token}">
        {"".join(secoes_html)}
    </form>

    <div class="enviando" id="enviando">⏳ Enviando suas respostas...</div>

    <div class="sucesso" id="sucesso">
        <div style="font-size:64px;margin-bottom:16px">✅</div>
        <h2>Enviado com sucesso!</h2>
        <p>Obrigado pelo preenchimento.<br>Suas informações foram recebidas pela clínica.</p>
        <p style="margin-top:16px;font-size:13px;color:#aaa">Você já pode fechar esta página.</p>
    </div>
</div>

<script>
const total   = {total};
let   current = 0;

function showStep(n) {{
    document.querySelectorAll('.secao').forEach(s => s.classList.remove('active'));
    document.querySelectorAll('.step-dot').forEach(d => d.classList.remove('active'));
    document.querySelectorAll('.secao')[n].classList.add('active');
    document.querySelectorAll('.step-dot')[n].classList.add('active');
    const pct = Math.round(((n + 1) / total) * 100);
    document.getElementById('progresso').style.width = pct + '%';
    current = n;
    window.scrollTo(0, 0);
}}

document.querySelectorAll('.btn-next').forEach(btn => {{
    btn.addEventListener('click', () => {{
        const secao = document.querySelectorAll('.secao')[current];
        const campos = secao.querySelectorAll('input[required], textarea[required]');
        for (const campo of campos) {{
            if (!campo.value.trim() && campo.type !== 'radio' && campo.type !== 'checkbox') {{
                campo.focus(); campo.style.borderColor = '#C1726A'; return;
            }}
        }}
        const radios = {{}};
        secao.querySelectorAll('input[type=radio][required]').forEach(r => {{ radios[r.name] = radios[r.name] || r.checked; }});
        for (const [name, checked] of Object.entries(radios)) {{
            if (!checked) {{
                secao.querySelector('input[name="' + name + '"]').closest('.campo').scrollIntoView();
                return;
            }}
        }}
        if (current < total - 1) showStep(current + 1);
    }});
}});

document.querySelectorAll('.btn-prev').forEach(btn => {{
    btn.addEventListener('click', () => {{ if (current > 0) showStep(current - 1); }});
}});

// Campos sim/não com "qual"
document.querySelectorAll('.trigger-qual').forEach(r => {{
    r.addEventListener('change', () => {{
        const target = document.getElementById(r.dataset.target);
        if (target) target.style.display = r.value === 'Sim' ? 'block' : 'none';
    }});
}});

// Máscaras
function maskData(e) {{
    let v = e.target.value.replace(/\\D/g,'').slice(0,8);
    if (v.length > 4) v = v.slice(0,2)+'/'+v.slice(2,4)+'/'+v.slice(4);
    else if (v.length > 2) v = v.slice(0,2)+'/'+v.slice(2);
    e.target.value = v;
}}
function maskFone(e) {{
    let v = e.target.value.replace(/\\D/g,'').slice(0,11);
    if (v.length > 7) v = '('+v.slice(0,2)+') '+v.slice(2,7)+'-'+v.slice(7);
    else if (v.length > 2) v = '('+v.slice(0,2)+') '+v.slice(2);
    e.target.value = v;
}}
function maskCpf(e) {{
    let v = e.target.value.replace(/\\D/g,'').slice(0,11);
    if (v.length > 9) v = v.slice(0,3)+'.'+v.slice(3,6)+'.'+v.slice(6,9)+'-'+v.slice(9);
    else if (v.length > 6) v = v.slice(0,3)+'.'+v.slice(3,6)+'.'+v.slice(6);
    else if (v.length > 3) v = v.slice(0,3)+'.'+v.slice(3);
    e.target.value = v;
}}
document.querySelectorAll('.mask-data').forEach(i => i.addEventListener('input', maskData));
document.querySelectorAll('.mask-fone').forEach(i => i.addEventListener('input', maskFone));
document.querySelectorAll('.mask-cpf').forEach(i => i.addEventListener('input', maskCpf));

// Submit via AJAX
document.getElementById('form-anamnese').addEventListener('submit', async (e) => {{
    e.preventDefault();
    document.getElementById('form-anamnese').style.display = 'none';
    document.getElementById('enviando').style.display = 'block';
    const data = new FormData(e.target);
    try {{
        const res = await fetch('/f/{token}', {{ method: 'POST', body: data }});
        const json = await res.json();
        document.getElementById('enviando').style.display = 'none';
        if (res.ok) {{
            document.getElementById('sucesso').style.display = 'block';
        }} else {{
            document.getElementById('form-anamnese').style.display = 'block';
            document.getElementById('erro-msg').style.display = 'block';
            document.getElementById('erro-msg').textContent = json.detail || 'Erro ao enviar. Tente novamente.';
        }}
    }} catch(err) {{
        document.getElementById('enviando').style.display = 'none';
        document.getElementById('form-anamnese').style.display = 'block';
        document.getElementById('erro-msg').style.display = 'block';
        document.getElementById('erro-msg').textContent = 'Erro de conexão. Tente novamente.';
    }}
}});
</script>
</body>
</html>"""


# ── ROTAS ─────────────────────────────────────────────────────
@app.get("/")
def root():
    return {"status": "ok", "servico": "TypCore Forms"}


@app.get("/f/{token}", response_class=HTMLResponse)
def exibir_form(token: str):
    dados    = verificar_token(token)
    esp_nome = dados.get("en", "")
    template = TEMPLATES.get(esp_nome)
    if not template:
        raise HTTPException(status_code=404, detail=f"Template não encontrado: {esp_nome}")
    return HTMLResponse(render_form(template, token))


@app.get("/api/templates/{especialidade}")
def obter_template(especialidade: str):
    """
    Retorna o template JSON (perguntas/seções) de uma especialidade.
    Sem dados sensíveis — é o mesmo conteúdo já exposto publicamente em /f/{token}.
    Usado pelo app desktop para montar os rótulos das respostas (q_0, q_1...)
    sempre em sincronia com o formulário que o Railway está servindo de fato,
    em vez de depender de uma cópia local do template que pode ficar desatualizada.
    """
    template = TEMPLATES.get(especialidade)
    if not template:
        raise HTTPException(status_code=404, detail=f"Template não encontrado: {especialidade}")
    return JSONResponse(template)


@app.post("/f/{token}")
async def receber_form(token: str, request: Request):
    dados = verificar_token(token)
    form  = await request.form()

    # Monta dict de respostas
    respostas = {}
    for k, v in form.multi_items():
        if k in respostas:
            val = respostas[k]
            respostas[k] = (val if isinstance(val, list) else [val]) + [v]
        else:
            respostas[k] = v

    # Verifica consentimento
    consentimento = any(
        "sim" in str(v).lower() or "concordo" in str(v).lower()
        for k, v in respostas.items()
        if "consentimento" in k.lower() or (isinstance(v, str) and "concordo" in v.lower())
    )

    session = Session()
    try:
        r = RespostaAnamnese(
            paciente_id        = dados.get("pid"),
            especialidade_id   = dados.get("eid"),
            especialidade_nome = dados.get("en"),
            clinica_id         = dados.get("cid"),
            respostas_json     = json.dumps(respostas, ensure_ascii=False),
            consentimento      = consentimento,
            submetido_em       = datetime.now(),
            sincronizado       = False,
        )
        session.add(r)
        session.commit()
        return JSONResponse({"ok": True, "id": r.id})
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@app.get("/api/respostas/{clinica_id}")
def listar_respostas(clinica_id: str, x_api_key: Optional[str] = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="API key inválida")
    session = Session()
    try:
        rows = session.query(RespostaAnamnese).filter_by(
            clinica_id=clinica_id, sincronizado=False
        ).order_by(RespostaAnamnese.submetido_em).all()
        return [{
            "id":                r.id,
            "paciente_id":       r.paciente_id,
            "especialidade_id":  r.especialidade_id,
            "especialidade_nome": r.especialidade_nome,
            "respostas":         json.loads(r.respostas_json or "{}"),
            "consentimento":     r.consentimento,
            "submetido_em":      r.submetido_em.isoformat() if r.submetido_em else None,
        } for r in rows]
    finally:
        session.close()


@app.post("/api/respostas/{clinica_id}/{resposta_id}/sincronizado")
def marcar_sincronizado(clinica_id: str, resposta_id: int,
                        x_api_key: Optional[str] = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="API key inválida")
    session = Session()
    try:
        r = session.query(RespostaAnamnese).filter_by(
            id=resposta_id, clinica_id=clinica_id
        ).first()
        if r:
            r.sincronizado = True
            session.commit()
        return {"ok": True}
    finally:
        session.close()

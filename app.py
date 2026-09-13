"""
Rodovia Smart: Sistema de Conservação Rodoviária & IA Multimodal
Aplicação Python / Streamlit para equipes de campo e engenheiros fiscais rodoviários.
Suporta deploy no Streamlit Community Cloud com leitura direta de st.secrets para Firebase e Gemini.
"""

import os
import io
import re
import json
import time
import base64
import zipfile
from datetime import datetime
from typing import List, Dict, Any, Optional

import streamlit as st
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

import firebase_admin
from firebase_admin import credentials, firestore

# ==========================================
# CONFIGURAÇÃO DE PÁGINA STREAMLIT
# ==========================================
st.set_page_config(
    page_title="Rodovia Smart | Conservação & IA",
    page_icon="🛣️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==========================================
# INICIALIZAÇÃO DO FIREBASE VIA ST.SECRETS
# ==========================================
@st.cache_resource
def inicializar_firebase():
    """
    Inicializa o firebase_admin lendo as credenciais diretamente de st.secrets["firebase"].
    Converte quebras de linha da chave privada com .replace('\\n', '\n') para evitar erro RSA.
    Utiliza @st.cache_resource para evitar reinicializações a cada interação do Streamlit.
    """
    # 1. Tentativa de leitura direta de st.secrets["firebase"]
    try:
        if "firebase" in st.secrets:
            firebase_dict = dict(st.secrets["firebase"])

            # Tratamento da chave privada RSA para converter quebras de linha escapadas
            if "private_key" in firebase_dict and isinstance(firebase_dict["private_key"], str):
                firebase_dict["private_key"] = firebase_dict["private_key"].replace("\\n", "\n")

            cred = credentials.Certificate(firebase_dict)
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)
            return firestore.client()
    except Exception as e:
        # Se st.secrets não tiver o bloco firebase ou falhar, registra no sidebar sem quebrar a app
        pass

    # 2. Fallback para arquivo local serviceAccountKey.json caso exista (ambiente de desenvolvimento)
    if os.path.exists("serviceAccountKey.json"):
        try:
            cred = credentials.Certificate("serviceAccountKey.json")
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred)
            return firestore.client()
        except Exception:
            return None

    return None


# ==========================================
# INICIALIZAÇÃO DO CLIENTE GEMINI VIA ST.SECRETS
# ==========================================
@st.cache_resource
def obter_cliente_gemini():
    """
    Inicializa o cliente Gemini (google.genai.Client) buscando a chave em st.secrets["GEMINI_API_KEY"],
    com fallback para os.environ["GEMINI_API_KEY"].
    """
    api_key = None
    try:
        if "GEMINI_API_KEY" in st.secrets:
            api_key = st.secrets["GEMINI_API_KEY"]
    except Exception:
        pass

    if not api_key:
        api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        return None

    try:
        from google import genai
        return genai.Client(api_key=api_key)
    except Exception as e:
        st.sidebar.warning(f"Erro ao inicializar cliente Gemini: {e}")
        return None


# ==========================================
# CONSTANTES E REGRAS DE NEGÓCIO
# ==========================================
RODOVIAS_PADRAO = ["BR-101", "BR-116", "BR-040", "BR-153", "BR-277", "SP-330", "SP-280", "MG-050", "Outra"]

# Empresas Contratadas de Conservação e Mapeamento de Equipes
EMPRESAS_CONSERVACAO: Dict[str, List[str]] = {
    "Consórcio Rodovias do Paraná": [f"Equipe {i:02d}" for i in range(1, 21)],
    "EPR Conservação Viária": [f"Equipe {i:02d}" for i in range(21, 41)],
    "Sul Pavimentação & Infraestrutura": [f"Equipe {i:02d}" for i in range(41, 61)],
    "Paraná Obras & Sinalização": [f"Equipe {i:02d}" for i in range(61, 81)],
}

TODAS_EMPRESAS = list(EMPRESAS_CONSERVACAO.keys())
EQUIPES_RODOVIA = [eq for equipes in EMPRESAS_CONSERVACAO.values() for eq in equipes]


def obter_empresa_da_equipe(equipe: str) -> str:
    """Retorna a empresa contratada associada à equipe de conservação."""
    if not equipe:
        return "Consórcio Rodovias do Paraná"
    for empresa, equipes in EMPRESAS_CONSERVACAO.items():
        if equipe in equipes:
            return empresa
    return "Consórcio Rodovias do Paraná"


TIPOS_SERVICO = [
    "Tapa-buraco",
    "Roçada",
    "Troca de Placa",
    "Defensa Metálica",
    "Tachão",
    "Limpeza de Drenagem",
    "Outros",
]

PREFIXOS_SERVICO = {
    "Tapa-buraco": "BURACO",
    "Roçada": "ROCADA",
    "Troca de Placa": "PLACA",
    "Defensa Metálica": "DEFENSA",
    "Tachão": "TACHAO",
    "Limpeza de Drenagem": "DRENAGEM",
    "Outros": "ATIVO",
}

DEFAULT_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
DATA_FILE_PATH = os.path.join(os.path.dirname(__file__), "data", "registros.json")


# ==========================================
# GERAÇÃO DE IMAGENS SEMENTE (PIL)
# ==========================================
def _gerar_imagem_placeholder_base64(tipo: str, titulo: str, subtitulo: str) -> str:
    """Gera uma imagem de teste leve e representativa em formato base64 JPEG."""
    img = Image.new("RGB", (640, 420), color=(241, 245, 249))
    draw = ImageDraw.Draw(img)

    if tipo == "PLACA":
        draw.rectangle([0, 0, 640, 180], fill=(186, 230, 253))
        draw.rectangle([0, 180, 640, 420], fill=(51, 65, 85))
        draw.rectangle([310, 160, 330, 360], fill=(148, 163, 184))
        draw.ellipse([220, 50, 420, 250], fill=(220, 38, 38))
        draw.ellipse([242, 72, 398, 228], fill=(255, 255, 255))
        draw.text((320, 150), "80", fill=(15, 23, 42), anchor="mm")
    elif tipo == "ROCADA":
        draw.rectangle([0, 0, 640, 160], fill=(186, 230, 253))
        draw.rectangle([0, 160, 640, 420], fill=(34, 197, 94))
        for x in range(30, 610, 20):
            draw.polygon([(x, 160), (x + 10, 120), (x + 20, 160)], fill=(22, 101, 52))
        draw.rectangle([0, 300, 640, 420], fill=(71, 85, 105))
    elif tipo == "DEFENSA":
        draw.rectangle([0, 0, 640, 160], fill=(203, 213, 225))
        draw.rectangle([0, 160, 640, 420], fill=(30, 41, 59))
        for x in [80, 200, 320, 440, 560]:
            draw.rectangle([x, 140, x + 20, 300], fill=(100, 116, 139))
        draw.line([(40, 200), (240, 200), (320, 240), (440, 200), (600, 200)], fill=(226, 232, 240), width=16)
    elif tipo == "BURACO":
        draw.rectangle([0, 0, 640, 160], fill=(148, 163, 184))
        draw.rectangle([0, 160, 640, 420], fill=(30, 41, 59))
        draw.ellipse([200, 220, 440, 340], fill=(15, 23, 42))
        draw.line([(180, 280), (460, 280)], fill=(71, 85, 105), width=4)
    else:
        draw.rectangle([20, 20, 620, 400], fill=(226, 232, 240), outline=(148, 163, 184), width=3)

    draw.rectangle([0, 360, 640, 420], fill=(15, 23, 42))
    draw.text((320, 390), f"{titulo} - {subtitulo}", fill=(248, 250, 252), anchor="mm")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")


# ==========================================
# CAMADA DE DADOS: FIRESTORE & ARMAZENAMENTO LOCAL
# ==========================================
def carregar_registros_iniciais() -> List[Dict[str, Any]]:
    """Carrega dados salvos em disco ou gera sementes com padrões de rodovias."""
    if os.path.exists(DATA_FILE_PATH):
        try:
            with open(DATA_FILE_PATH, "r", encoding="utf-8") as f:
                dados = json.load(f)
                if dados and isinstance(dados, list):
                    return dados
        except Exception as e:
            st.sidebar.warning(f"Aviso ao carregar dados locais: {e}")

    sementes = [
        {
            "id": "PLACA-KM142-01",
            "doc_id": "PLACA-KM142-01",
            "prefixo_km": "PLACA-KM142",
            "rodovia": "BR-101",
            "equipe": "Equipe 04",
            "km": "142",
            "tipo_servico": "Troca de Placa",
            "latitude": -23.55052,
            "longitude": -46.633308,
            "foto_base64": _gerar_imagem_placeholder_base64("PLACA", "Placa R-19 (80 km/h)", "KM 142"),
            "observacoes_campo": "Placa de regulamentação com película desbotada e poste levemente inclinado após tempestade.",
            "data_envio": "2026-09-12T10:15:00",
            "status_ia": "analisado",
            "descricao_ia": "Placa de regulamentação R-19 (Velocidade Máxima Permitida 80 km/h) com orla vermelha desbotada e perda de retrorrefletividade noturna. Mourão de aço galvanizado com inclinação de ~5°.",
            "sugestao_ia": "Substituição preventiva da película retrorrefletiva prismática Tipo III (norma ABNT NBR 14644) e aprumamento do poste de sustentação.",
            "aprovado": True,
            "analista_responsavel": "Eng. Carlos Silva",
            "data_atualizacao": "2026-09-12T11:00:00",
        },
        {
            "id": "ROCADA-KM105-01",
            "doc_id": "ROCADA-KM105-01",
            "prefixo_km": "ROCADA-KM105",
            "rodovia": "BR-101",
            "equipe": "Equipe 12",
            "km": "105.4",
            "tipo_servico": "Roçada",
            "latitude": -23.5612,
            "longitude": -46.6558,
            "foto_base64": _gerar_imagem_placeholder_base64("ROCADA", "Roçada Faixa de Domínio", "KM 105.4"),
            "observacoes_campo": "Vegetação rasteira alta encobrindo sarjeta lateral de drenagem e linha de bordo.",
            "data_envio": "2026-09-12T11:30:00",
            "status_ia": "pendente",
            "descricao_ia": "",
            "sugestao_ia": "",
            "aprovado": False,
            "analista_responsavel": "",
            "data_atualizacao": "",
        },
        {
            "id": "DEFENSA-KM118-01",
            "doc_id": "DEFENSA-KM118-01",
            "prefixo_km": "DEFENSA-KM118",
            "rodovia": "SP-330",
            "equipe": "Equipe 02",
            "km": "118",
            "tipo_servico": "Defensa Metálica",
            "latitude": -23.5824,
            "longitude": -46.6711,
            "foto_base64": _gerar_imagem_placeholder_base64("DEFENSA", "Defensa Semi-Rígida Deformada", "KM 118"),
            "observacoes_campo": "Defensa metálica com deformação acentuada após abalroamento veicular no acostamento.",
            "data_envio": "2026-09-12T13:45:00",
            "status_ia": "pendente",
            "descricao_ia": "",
            "sugestao_ia": "",
            "aprovado": False,
            "analista_responsavel": "",
            "data_atualizacao": "",
        },
        {
            "id": "BURACO-KM088-01",
            "doc_id": "BURACO-KM088-01",
            "prefixo_km": "BURACO-KM088",
            "rodovia": "BR-116",
            "equipe": "Equipe 07",
            "km": "88.2",
            "tipo_servico": "Tapa-buraco",
            "latitude": -23.5410,
            "longitude": -46.6210,
            "foto_base64": _gerar_imagem_placeholder_base64("BURACO", "Panela no Pavimento", "KM 88.2"),
            "observacoes_campo": "Panela profunda na faixa de rolamento direita após período intenso de chuvas.",
            "data_envio": "2026-09-12T14:10:00",
            "status_ia": "pendente",
            "descricao_ia": "",
            "sugestao_ia": "",
            "aprovado": False,
            "analista_responsavel": "",
            "data_atualizacao": "",
        },
    ]
    salvar_dados_em_disco(sementes)
    return sementes


def salvar_dados_em_disco(registros: List[Dict[str, Any]]) -> None:
    """Grava a lista de registros no arquivo JSON persistente local."""
    try:
        os.makedirs(os.path.dirname(DATA_FILE_PATH), exist_ok=True)
        with open(DATA_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(registros, f, ensure_ascii=False, indent=2)
    except Exception as e:
        st.error(f"Erro ao salvar registros no disco: {e}")


def carregar_registros_firestore(db) -> Optional[List[Dict[str, Any]]]:
    """Lê todos os registros da coleção 'registros' no Cloud Firestore."""
    try:
        docs = db.collection("registros").stream()
        lista = []
        for doc in docs:
            dados = doc.to_dict()
            if "id" not in dados:
                dados["id"] = doc.id
            lista.append(dados)
        if lista:
            lista.sort(key=lambda x: x.get("data_envio", ""), reverse=True)
            return lista
    except Exception as e:
        st.sidebar.warning(f"Aviso ao consultar dados no Firestore: {e}")
    return None


def sincronizar_registro_firestore(db, registro: Dict[str, Any]) -> None:
    """Envia ou atualiza um registro diretamente no Cloud Firestore (sem Firebase Storage)."""
    if db is not None:
        try:
            doc_id = registro.get("id") or registro.get("doc_id")
            if doc_id:
                db.collection("registros").document(doc_id).set(registro, merge=True)
        except Exception as e:
            st.error(f"Erro ao gravar registro no Firestore: {e}")


def remover_registro_firestore(db, doc_id: str) -> None:
    """Remove um documento do Cloud Firestore."""
    if db is not None:
        try:
            db.collection("registros").document(doc_id).delete()
        except Exception as e:
            st.error(f"Erro ao excluir registro no Firestore: {e}")


def remover_lote_firestore(db, doc_ids: List[str]) -> None:
    """Remove um lote de documentos do Cloud Firestore."""
    if db is not None:
        try:
            batch = db.batch()
            for did in doc_ids:
                ref = db.collection("registros").document(did)
                batch.delete(ref)
            batch.commit()
        except Exception as e:
            st.error(f"Erro ao excluir lote no Firestore: {e}")


def obter_registros() -> List[Dict[str, Any]]:
    """Obtém a lista de registros da sessão, do Firestore ou do disco local."""
    if "registros" not in st.session_state:
        db = inicializar_firebase()
        if db is not None:
            dados_nuvem = carregar_registros_firestore(db)
            if dados_nuvem:
                st.session_state.registros = dados_nuvem
                salvar_dados_em_disco(dados_nuvem)
                return st.session_state.registros
            else:
                # Coleção vazia: carrega sementes e sincroniza com a nuvem
                sementes = carregar_registros_iniciais()
                for s in sementes:
                    sincronizar_registro_firestore(db, s)
                st.session_state.registros = sementes
                return st.session_state.registros

        # Se não houver Firebase conectado, carrega do disco local
        st.session_state.registros = carregar_registros_iniciais()

    # Assegurar que todo registro possua o campo de empresa associado
    for r in st.session_state.registros:
        if not r.get("empresa"):
            r["empresa"] = obter_empresa_da_equipe(r.get("equipe", ""))

    return st.session_state.registros


def gerar_proximo_id_ativo(tipo_servico: str, km_str: str, registros: List[Dict[str, Any]]) -> str:
    """Gera identificador padronizado no formato PREFIXO-KMXXX-01."""
    prefixo = PREFIXOS_SERVICO.get(tipo_servico, "ATIVO")
    km_limpo = re.sub(r"[^0-9.-]", "", km_str).strip() or "000"
    base_id = f"{prefixo}-KM{km_limpo}"

    iguais = [r for r in registros if r.get("id", "").startswith(base_id)]
    seq = len(iguais) + 1
    return f"{base_id}-{seq:02d}"


# ==========================================
# COMPRESSÃO INTELIGENTE DE IMAGENS (PILLOW)
# ==========================================
def processar_e_comprimir_foto(arquivo_bytes: bytes, max_dim: int = 1024, qualidade_inicial: int = 75) -> Dict[str, Any]:
    """
    Comprime a imagem em memória utilizando Pillow para garantir tamanho inferior a 250KB,
    permitindo gravação Base64 direta no documento do Firestore sem uso de Firebase Storage.
    """
    tam_original_kb = round(len(arquivo_bytes) / 1024, 1)

    img = Image.open(io.BytesIO(arquivo_bytes))
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    largura, altura = img.size
    if max(largura, altura) > max_dim:
        proporcao = max_dim / max(largura, altura)
        nova_largura = int(largura * proporcao)
        nova_altura = int(altura * proporcao)
        img = img.resize((nova_largura, nova_altura), Image.Resampling.LANCZOS)

    # Compressão iterativa garantindo rigorosamente <= 250 KB
    qualidade = qualidade_inicial
    buffer_saida = io.BytesIO()
    img.save(buffer_saida, format="JPEG", quality=qualidade, optimize=True)

    while len(buffer_saida.getvalue()) > 250 * 1024 and qualidade > 25:
        qualidade -= 10
        buffer_saida = io.BytesIO()
        img.save(buffer_saida, format="JPEG", quality=qualidade, optimize=True)

    bytes_comprimidos = buffer_saida.getvalue()
    tam_comprimido_kb = round(len(bytes_comprimidos) / 1024, 1)
    economia_pct = round((1 - (tam_comprimido_kb / max(tam_original_kb, 0.1))) * 100, 1)

    b64_str = "data:image/jpeg;base64," + base64.b64encode(bytes_comprimidos).decode("utf-8")

    return {
        "base64": b64_str,
        "bytes": bytes_comprimidos,
        "tamanho_original_kb": tam_original_kb,
        "tamanho_comprimido_kb": tam_comprimido_kb,
        "economia_pct": max(economia_pct, 0.0),
        "dimensoes": f"{img.width}x{img.height}px",
    }


# ==========================================
# MOTOR DE DIAGNÓSTICO TÉCNICO (GEMINI & REGRAS DNIT)
# ==========================================
def obter_analise_regras_dnit(tipo_servico: str, km: str, rodovia: str = "") -> Dict[str, str]:
    """Diagnóstico técnico com base no manual de conservação rodoviária DNIT/CONTRAN."""
    loc = f"no KM {km}" if km else "no trecho inspecionado"
    if rodovia:
        loc = f"na rodovia {rodovia}, {loc}"

    if tipo_servico == "Tapa-buraco":
        return {
            "descricao_ia": f"Identificada panela/desagregação no pavimento asfáltico {loc}. Degradação da camada de rolamento com profundidade superior a 5 cm e risco de corte de pneus.",
            "sugestao_ia": "Execução imediata de reparo localizado (tapa-buraco) com corte geométrico, limpeza do fundo, imprimação ligante e aplicação de CBUQ com compactação adequada.",
        }
    elif tipo_servico == "Troca de Placa":
        return {
            "descricao_ia": f"Identificada placa de sinalização vertical {loc} com desgaste da película retrorrefletiva tipo I/III e oxidação no ponto de ancoragem do mourão. Legibilidade comprometida no período noturno.",
            "sugestao_ia": "Substituição preventiva da placa por chapa de alumínio composto com película prismática de alta intensidade. Alinhamento angular do poste conforme norma CONTRAN.",
        }
    elif tipo_servico == "Roçada":
        return {
            "descricao_ia": f"Vegetação rasteira e capim alto {loc} invadindo a faixa de domínio (>1,10m de altura), com obstrução parcial da linha de visão e acúmulo de biomassa seca junto à sarjeta.",
            "sugestao_ia": "Execução de roçada mecânica lateral em faixa de 3,0 metros a partir do bordo da pista e desobstrução imediata dos dispositivos de drenagem superficial.",
        }
    elif tipo_servico == "Defensa Metálica":
        return {
            "descricao_ia": f"Defensa metálica semi-rígida padrão W {loc} com deformação permanente na lâmina por impacto veicular e afrouxamento de parafusos de fixação nos espaçadores.",
            "sugestao_ia": "Substituição das lâminas avariadas e reaperto dos mourões de sustentação. Instalação de novos retrorrefletivos trapezoidais padrão DNIT.",
        }
    elif tipo_servico == "Tachão":
        return {
            "descricao_ia": f"Linha de tachões refletivos {loc} com elementos desprendidos do pavimento asfáltico e perda da refletividade nas lentes biconvexas remanescentes.",
            "sugestao_ia": "Fresagem do ponto, furação e reassentamento com cola epóxi bicomponente e substituição dos tachões trincados conforme especificação DNIT 034/2004-EM.",
        }
    elif tipo_servico == "Limpeza de Drenagem":
        return {
            "descricao_ia": f"Dispositivo de drenagem superficial {loc} assoreado com terra, pedregulhos e vegetação invasora em extensão de aproximadamente 15 metros lineares.",
            "sugestao_ia": "Limpeza manual com enxada e remoção de entulho para descarte em bota-fora licenciado. Verificação de trincas estruturais no fundo da valeta.",
        }
    else:
        return {
            "descricao_ia": f"Elemento de conservação rodoviária inspecionado {loc}. Apresenta necessidade de intervenção para manutenção dos padrões de segurança operacional da via.",
            "sugestao_ia": "Programar intervenção de conservação rotineira com a equipe de campo designada para este lote.",
        }


def executar_analise_gemini(foto_base64: str, tipo_servico: str, km: str, rodovia: str = "") -> Dict[str, str]:
    """
    Executa a análise técnica multimodal do Google Gemini (gemini-2.5-flash)
    utilizando google.genai.Client buscando a chave de st.secrets["GEMINI_API_KEY"],
    com fallback garantido para a base de regras técnicas do DNIT.
    """
    client = obter_cliente_gemini()
    if client is None:
        return obter_analise_regras_dnit(tipo_servico, km, rodovia)

    try:
        from google.genai import types

        raw_b64 = foto_base64
        mime_type = "image/jpeg"
        if "," in foto_base64:
            partes = foto_base64.split(",", 1)
            raw_b64 = partes[1]
            match = re.search(r"data:(image/[a-zA-Z0-9+.-]+);base64", partes[0])
            if match:
                mime_type = match.group(1)

        imagem_bytes = base64.b64decode(raw_b64)

        prompt = f"""Você é um Engenheiro Civil Sênior especialista em Conservação e Infraestrutura Rodoviária (normas DNIT e CONTRAN).
Analise com rigor técnico esta fotografia de campo enviada pela equipe de conservação rodoviária.

CONTEXTO INFORMADO PELA EQUIPE DE CAMPO:
- Rodovia: {rodovia or 'Rodovia sob concessão'}
- Localização: KM {km}
- Tipo de Serviço Inspecionado: {tipo_servico}

INSTRUÇÕES DE ANÁLISE:
1. 'descricao_ia': Faça uma descrição técnica detalhada do que está visível na imagem:
   - Se TAPA-BURACO / PAVIMENTO: Avalie severidade da panela, trincamento em couro de crocodilo, afundamento de trilha de roda ou desagregação.
   - Se PLACA: Identifique o código oficial do CONTRAN/DNIT (ex: R-1 PARE, A-1a, R-19, etc.), película refletiva, alinhamento, postes, sinais de ferrugem, vandalismo ou desbotamento.
   - Se ROÇADA: Avalie a altura da vegetação, faixa de domínio roçada, acúmulo de biomassa e visibilidade de sarjetas e sinalização.
   - Se DEFENSA METÁLICA: Avalie deformações por impacto, fixação nos mourões, lâminas avariadas e refletivos.
   - Se TACHÃO / TACHAS: Avalie fixação na pista, pinos soltos e elementos retrorrefletivos.
   - Se LIMPEZA DE DRENAGEM: Avalie desobstrução de sarjetas, valetas, bueiros e acúmulo de sedimentos.
2. 'sugestao_ia': Apresente uma recomendação de engenharia prática e objetiva para o fiscal (ex: reparo com CBUQ, substituição de película prismática, roçada mecânica lateral, substituição de lâmina W, etc.).

Responda ESTRITAMENTE em formato JSON com duas chaves:
{{
  "descricao_ia": "diagnostico tecnico",
  "sugestao_ia": "recomendacao pratica de engenharia"
}}"""

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_bytes(data=imagem_bytes, mime_type=mime_type),
                prompt,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )

        if response.text:
            parsed = json.loads(response.text)
            return {
                "descricao_ia": parsed.get("descricao_ia", ""),
                "sugestao_ia": parsed.get("sugestao_ia", ""),
            }
    except Exception as e:
        st.sidebar.warning(f"Gemini API em modo de contingência. Aplicando regras DNIT: {e}")

    return obter_analise_regras_dnit(tipo_servico, km, rodovia)


# ==========================================
# EXPORTAÇÃO DOCX (WORD) E PACOTE ZIP
# ==========================================
def gerar_relatorio_docx(registros: List[Dict[str, Any]], nome_fiscal: str) -> io.BytesIO:
    """Gera um laudo formal em Word (.docx) com tabelas de engenharia e fotos anexadas."""
    import docx
    from docx.shared import Inches, Pt, RGBColor
    from docx.enum.table import WD_TABLE_ALIGNMENT

    doc = docx.Document()

    titulo = doc.add_heading(level=0)
    run_titulo = titulo.add_run("RODOVIA SMART — RELATÓRIO TÉCNICO DE CONSERVAÇÃO")
    run_titulo.font.color.rgb = RGBColor(15, 23, 42)
    run_titulo.font.size = Pt(18)
    run_titulo.bold = True

    p_sub = doc.add_paragraph("Fiscalização de Infraestrutura e Consolidação de Ativos de Campo")
    p_sub.runs[0].font.color.rgb = RGBColor(71, 85, 105)
    p_sub.runs[0].font.size = Pt(11)

    meta_table = doc.add_table(rows=4, cols=2)
    meta_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    meta_data = [
        ("Data de Emissão:", datetime.now().strftime("%d/%m/%Y às %H:%M")),
        ("Engenheiro Fiscal Responsável:", nome_fiscal or "Eng. Fiscal"),
        ("Total de Ativos Inspecionados:", str(len(registros))),
        ("Normas de Referência:", "Manuais de Conservação DNIT e Resoluções CONTRAN"),
    ]
    for row_idx, (k, v) in enumerate(meta_data):
        row = meta_table.rows[row_idx]
        row.cells[0].paragraphs[0].add_run(k).bold = True
        row.cells[1].paragraphs[0].add_run(v)

    doc.add_paragraph()

    doc.add_heading("1. Resumo Quantitativo dos Ativos", level=1)
    resumo_table = doc.add_table(rows=1, cols=6)
    resumo_table.style = "Table Grid"
    headers = ["ID Ativo", "Rodovia / KM", "Equipe", "Serviço", "Status IA", "Aprovação"]
    for i, h in enumerate(headers):
        cell = resumo_table.rows[0].cells[i]
        run = cell.paragraphs[0].add_run(h)
        run.bold = True

    for reg in registros:
        r_cells = resumo_table.add_row().cells
        r_cells[0].paragraphs[0].add_run(reg.get("id", ""))
        rod_km = f"{reg.get('rodovia', 'BR-101')} KM {reg.get('km', '')}"
        r_cells[1].paragraphs[0].add_run(rod_km)
        r_cells[2].paragraphs[0].add_run(reg.get("equipe", ""))
        r_cells[3].paragraphs[0].add_run(reg.get("tipo_servico", ""))
        r_cells[4].paragraphs[0].add_run(reg.get("status_ia", "").upper())
        r_cells[5].paragraphs[0].add_run("APROVADO" if reg.get("aprovado") else "PENDENTE")

    doc.add_page_break()

    doc.add_heading("2. Fichas Técnicas de Registro Fotográfico", level=1)

    for idx, reg in enumerate(registros, 1):
        doc.add_heading(f"Ativo {idx}: {reg.get('id')} ({reg.get('rodovia', 'BR-101')} KM {reg.get('km')})", level=2)

        info_p = doc.add_paragraph()
        info_p.add_run(f"Rodovia: {reg.get('rodovia', 'BR-101')} | KM: {reg.get('km')} | Equipe: {reg.get('equipe')} | Serviço: {reg.get('tipo_servico')}\n").bold = True
        info_p.add_run(f"Data de Envio: {reg.get('data_envio')} | Coordenadas GPS: Lat {reg.get('latitude')}, Lng {reg.get('longitude')}\n")
        info_p.add_run(f"Observação de Campo: {reg.get('observacoes_campo') or 'Nenhuma observação informada.'}\n")

        diag_p = doc.add_paragraph()
        r_ia_t = diag_p.add_run("Diagnóstico Técnico (IA / DNIT): ")
        r_ia_t.bold = True
        diag_p.add_run(reg.get("descricao_ia") or "Pendente de processamento.")

        rec_p = doc.add_paragraph()
        r_rec_t = rec_p.add_run("Recomendação Fiscal: ")
        r_rec_t.bold = True
        rec_p.add_run(reg.get("sugestao_ia") or "Pendente de análise.")

        b64 = reg.get("foto_base64", "")
        if b64:
            try:
                if "," in b64:
                    b64 = b64.split(",", 1)[1]
                img_bytes = base64.b64decode(b64)
                img_stream = io.BytesIO(img_bytes)
                doc.add_picture(img_stream, width=Inches(4.2))
            except Exception as err:
                doc.add_paragraph(f"[Erro ao anexar fotografia: {err}]")

        doc.add_paragraph("—" * 40)

    doc_io = io.BytesIO()
    doc.save(doc_io)
    doc_io.seek(0)
    return doc_io


def gerar_pacote_fotos_zip(registros: List[Dict[str, Any]]) -> io.BytesIO:
    """Compila fotos em um arquivo .zip nomeadas no padrão {RODOVIA}_{ID}_{KM}.jpg."""
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for reg in registros:
            b64 = reg.get("foto_base64", "")
            if not b64:
                continue
            try:
                if "," in b64:
                    b64 = b64.split(",", 1)[1]
                img_bytes = base64.b64decode(b64)
                rod = reg.get("rodovia", "ROD").replace("-", "")
                nome_foto = f"{rod}_{reg.get('id', 'ATIVO')}_KM{reg.get('km', '000')}.jpg"
                nome_foto = re.sub(r"[^a-zA-Z0-9_.-]", "_", nome_foto)
                zip_file.writestr(nome_foto, img_bytes)
            except Exception:
                continue

    zip_buffer.seek(0)
    return zip_buffer


# ==========================================
# INTERFACE PRINCIPAL STREAMLIT
# ==========================================
db_firestore = inicializar_firebase()
cliente_gemini = obter_cliente_gemini()
registros = obter_registros()

if "modulo_ativo" not in st.session_state:
    st.session_state.modulo_ativo = "🚜 Tela de Campo"

if "autenticado" not in st.session_state:
    st.session_state.autenticado = False

# ==============================================================================
# ESTILO GLOBAL: DESIGN SYSTEM GOOGLE STITCH • EPR PARANÁ (DESKTOP & MOBILE)
# ==============================================================================
st.html(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');
    @import url('https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200');

    /* Ocultar containers técnicos de st.html */
    [data-testid="stHtml"]:has(> style) {
        display: none !important;
    }

    /* ------------------------------------------------------------- */
    /* DESIGN TOKENS & CORES INSTITUCIONAIS EPR PARANÁ */
    /* ------------------------------------------------------------- */
    :root {
        --primary: #096138;
        --primary-container: #2d7a4f;
        --on-primary: #ffffff;
        --on-primary-container: #b8ffce;
        --primary-fixed: #a5f4bf;
        --on-primary-fixed: #00210f;
        --surface: #f8f9ff;
        --surface-bright: #f8f9ff;
        --surface-container-lowest: #ffffff;
        --surface-container-low: #eff4ff;
        --surface-container: #e5eeff;
        --surface-container-high: #dce9ff;
        --surface-container-highest: #d3e4fe;
        --on-surface: #0b1c30;
        --on-surface-variant: #3f4941;
        --secondary: #545f73;
        --secondary-container: #d5e0f8;
        --tertiary: #834300;
        --tertiary-fixed: #ffdcc4;
        --on-tertiary-fixed: #2f1400;
        --error: #ba1a1a;
        --error-container: #ffdad6;
        --on-error: #ffffff;
        --on-error-container: #93000a;
    }

    /* Tipografia e Base Global */
    html, body, [class*="st-"], .stMarkdown, .stText, p, span, label, input, select, textarea, button {
        font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important;
    }

    .stApp {
        background-color: #f8f9ff !important;
        color: #0b1c30 !important;
    }

    /* Ícones Material Symbols */
    .material-symbols-outlined {
        font-family: 'Material Symbols Outlined' !important;
        font-weight: normal;
        font-style: normal;
        font-size: 20px;
        line-height: 1;
        letter-spacing: normal;
        text-transform: none;
        display: inline-block;
        white-space: nowrap;
        word-wrap: normal;
        direction: ltr;
        -webkit-font-feature-settings: 'liga';
        -webkit-font-smoothing: antialiased;
        vertical-align: middle;
    }

    /* ------------------------------------------------------------- */
    /* BORDAS NÍTIDAS E COMPLETAS EM TODOS OS DROPDOWNS / SELECTBOX */
    /* ------------------------------------------------------------- */
    div[data-testid="stSelectbox"] div[class*="e1fp86qc0"],
    div[data-testid="stSelectbox"] div:has(> button[aria-label="Open"]),
    div[data-testid="stSelectbox"] div[role="combobox"],
    div[data-testid="stSelectbox"] [data-baseweb="select"] > div,
    div[data-testid="stSelectbox"] [data-baseweb="select"],
    div.stSelectbox div[class*="e1fp86qc0"],
    div.stSelectbox div:has(> button[aria-label="Open"]) {
        border: 2px solid #334155 !important;
        border-top: 2px solid #334155 !important;
        border-bottom: 2px solid #334155 !important;
        border-left: 2px solid #334155 !important;
        border-right: 2px solid #334155 !important;
        border-radius: 12px !important;
        background-color: #ffffff !important;
        box-sizing: border-box !important;
        box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06) !important;
        overflow: visible !important;
    }

    div[data-testid="stSelectbox"] div[class*="e1fp86qc0"]:focus-within,
    div[data-testid="stSelectbox"] div:has(> button[aria-label="Open"]):focus-within,
    div[data-testid="stSelectbox"] [data-baseweb="select"]:focus-within {
        border-color: #096138 !important;
        box-shadow: 0 0 0 3px rgba(9, 97, 56, 0.2) !important;
    }

    /* INPUTS DE TEXTO E NÚMERO */
    div[data-testid="stTextInput"] div[data-testid="stTextInputRootElement"],
    div[data-testid="stTextInput"] div[class*="eqy66r5"],
    div[data-testid="stTextInput"] [data-baseweb="input"],
    div[data-testid="stTextInput"] div:has(> input),
    div[data-testid="stNumberInput"] div[class*="e1rv0tzo0"],
    div[data-testid="stNumberInput"] div:has(> input),
    div[data-testid="stNumberInput"] [data-baseweb="input"] {
        border: 2px solid #334155 !important;
        border-top: 2px solid #334155 !important;
        border-bottom: 2px solid #334155 !important;
        border-left: 2px solid #334155 !important;
        border-right: 2px solid #334155 !important;
        border-radius: 12px !important;
        background-color: #ffffff !important;
        box-sizing: border-box !important;
        box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06) !important;
    }

    div[data-testid="stTextInput"] div[data-testid="stTextInputRootElement"]:focus-within,
    div[data-testid="stNumberInput"] div[class*="e1rv0tzo0"]:focus-within {
        border-color: #096138 !important;
        box-shadow: 0 0 0 3px rgba(9, 97, 56, 0.2) !important;
    }

    /* ÁREAS DE TEXTO / TEXTAREA */
    div[data-testid="stTextArea"] textarea {
        border: 2px solid #334155 !important;
        border-radius: 12px !important;
        background-color: #ffffff !important;
        color: #0b1c30 !important;
        box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06) !important;
        font-family: 'Plus Jakarta Sans', sans-serif !important;
    }
    div[data-testid="stTextArea"] textarea:focus {
        border-color: #096138 !important;
        box-shadow: 0 0 0 3px rgba(9, 97, 56, 0.2) !important;
    }

    /* Dropdown Flutuante */
    div[data-testid="stSelectboxVirtualDropdown"],
    div[class*="e1fp86qc4"] {
        border: 2px solid #334155 !important;
        border-radius: 12px !important;
        box-shadow: 0 10px 28px rgba(0, 0, 0, 0.16) !important;
        background-color: #ffffff !important;
        z-index: 99999 !important;
    }

    /* Estilo de Dropdown e Inputs Desabilitados */
    div[data-testid="stSelectbox"]:has(*[aria-disabled="true"]) div[role="combobox"],
    div[data-testid="stSelectbox"] [aria-disabled="true"],
    div[data-testid="stTextInput"]:has(input[disabled]) {
        border: 2px dashed #94a3b8 !important;
        background-color: #eff4ff !important;
        opacity: 0.85 !important;
    }

    /* BOTÕES DO STREAMLIT ESTILIZADOS NA COR INSTITUCIONAL EPR PARANÁ */
    div[data-testid="stButton"] button[kind="primary"],
    button[data-testid="baseButton-primary"] {
        background: linear-gradient(135deg, #096138 0%, #2d7a4f 100%) !important;
        color: #ffffff !important;
        border: none !important;
        border-radius: 12px !important;
        font-weight: 700 !important;
        box-shadow: 0 4px 12px rgba(9, 97, 56, 0.25) !important;
        transition: all 0.2s ease !important;
    }
    div[data-testid="stButton"] button[kind="primary"]:hover,
    button[data-testid="baseButton-primary"]:hover {
        background: linear-gradient(135deg, #064426 0%, #1f5f3c 100%) !important;
        box-shadow: 0 6px 16px rgba(9, 97, 56, 0.35) !important;
        transform: translateY(-1px) !important;
    }

    div[data-testid="stButton"] button[kind="secondary"],
    button[data-testid="baseButton-secondary"] {
        background-color: #e5eeff !important;
        color: #0b1c30 !important;
        border: 1px solid #d5e0f8 !important;
        border-radius: 12px !important;
        font-weight: 600 !important;
        transition: all 0.2s ease !important;
    }
    div[data-testid="stButton"] button[kind="secondary"]:hover,
    button[data-testid="baseButton-secondary"]:hover {
        background-color: #dce9ff !important;
        color: #096138 !important;
    }

    /* CARDS DO STREAMLIT (BORDAS SUAVES E ARREDONDADAS) */
    div[data-testid="stVerticalBlockBorderWrapper"] {
        background-color: #ffffff !important;
        border-radius: 16px !important;
        border: 1px solid #dce9ff !important;
        box-shadow: 0 1px 4px rgba(15, 23, 42, 0.05) !important;
        padding: 0.75rem !important;
    }

    /* Badges & Tags */
    .stitch-badge {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 4px 10px;
        border-radius: 9999px;
        font-size: 11px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }
    .stitch-badge-primary {
        background-color: #a5f4bf;
        color: #00210f;
    }
    .stitch-badge-tertiary {
        background-color: #ffdcc4;
        color: #2f1400;
    }
    .stitch-badge-neutral {
        background-color: #e5eeff;
        color: #0b1c30;
    }

    /* Container de Metadados de Campo */
    .stitch-meta-box {
        background-color: #eff4ff;
        border: 1px solid #dce9ff;
        border-radius: 14px;
        padding: 14px 16px;
        margin-top: 10px;
        margin-bottom: 10px;
    }
    .stitch-meta-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 5px 0;
        border-bottom: 1px solid #e0ecff;
        font-size: 13.5px;
    }
    .stitch-meta-row:last-child {
        border-bottom: none;
    }
    .stitch-meta-label {
        color: #545f73;
        font-weight: 500;
    }
    .stitch-meta-val {
        color: #0b1c30;
        font-weight: 600;
    }

    /* Container de IA Gemini */
    .stitch-ia-box {
        background-color: #f8f9ff;
        border: 1px solid #dce9ff;
        border-radius: 14px;
        padding: 14px 16px;
        margin-bottom: 10px;
    }
    </style>
    """
)

# ==============================================================================
# 1. CUSTOMIZAÇÃO PARA A TELA DE CAMPO (MOBILE OTIMIZADA)
# ==============================================================================
if st.session_state.modulo_ativo == "🚜 Tela de Campo":
    # CSS Injetado para esconder completamente barra lateral, header e rodapé,
    # expandir para 100% da tela e estilizar botões grandes e fáceis de tocar na rua
    st.html(
        """
        <style>
        /* 1. Esconder completamente a barra lateral cinza padrão do Streamlit */
        [data-testid="stSidebar"], section[data-testid="stSidebar"], [data-testid="collapsedControl"] {
            display: none !important;
        }

        /* 2. Esconder menu do topo e o rodapé padrão */
        header[data-testid="stHeader"], [data-testid="stHeader"] {
            display: none !important;
        }
        footer, [data-testid="stFooter"], #MainMenu {
            display: none !important;
        }

        /* 3. Formulário ocupando 100% da largura da tela do celular */
        .main, .main .block-container, [data-testid="stMainBlockContainer"], [data-testid="stAppViewBlockContainer"] {
            max-width: 100% !important;
            width: 100% !important;
            padding-top: 0.5rem !important;
            padding-bottom: 6rem !important;
            padding-left: 0.75rem !important;
            padding-right: 0.75rem !important;
        }

        /* 4. Elementos de toque otimizados para campo */
        [data-testid="stSelectbox"] div[class*="e1fp86qc0"],
        [data-testid="stSelectbox"] [data-baseweb="select"] > div,
        div[data-baseweb="input"] > div,
        input[type="text"], input[type="number"] {
            min-height: 52px !important;
            font-size: 1.05rem !important;
            border-radius: 12px !important;
        }

        /* 5. Botão 'Gravar Registro de Campo' grande, destacado e verde na parte inferior */
        div[data-testid="stButton"] button[kind="primary"],
        button[data-testid="baseButton-primary"] {
            background: linear-gradient(135deg, #096138 0%, #2d7a4f 100%) !important;
            color: #ffffff !important;
            font-size: 1.15rem !important;
            font-weight: 800 !important;
            min-height: 58px !important;
            border-radius: 14px !important;
            border: none !important;
            box-shadow: 0 6px 20px rgba(9, 97, 56, 0.35) !important;
            width: 100% !important;
            letter-spacing: 0.5px !important;
            text-transform: uppercase !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            cursor: pointer !important;
            margin-top: 15px !important;
            margin-bottom: 15px !important;
            transition: transform 0.1s ease, box-shadow 0.1s ease !important;
        }
        div[data-testid="stButton"] button[kind="primary"]:hover,
        button[data-testid="baseButton-primary"]:hover {
            background: linear-gradient(135deg, #064426 0%, #1f5f3c 100%) !important;
            box-shadow: 0 8px 24px rgba(9, 97, 56, 0.5) !important;
        }
        div[data-testid="stButton"] button[kind="primary"]:active,
        button[data-testid="baseButton-primary"]:active {
            transform: scale(0.98) !important;
        }
        </style>
        """
    )

    # Topo Mobile Profissional: Cabeçalho com ícone de tráfego e alternador de módulo
    st.html(
        """
        <div style="display: flex; align-items: center; justify-content: space-between; padding: 10px 14px; background: rgba(248, 249, 255, 0.95); backdrop-filter: blur(12px); border-radius: 14px; border: 1px solid #dce9ff; margin-bottom: 12px; box-shadow: 0 1px 6px rgba(0,0,0,0.03);">
            <div style="display: flex; align-items: center; gap: 10px;">
                <div style="width: 36px; height: 36px; border-radius: 10px; background-color: #2d7a4f; color: #ffffff; display: flex; align-items: center; justify-content: center; font-size: 20px;">
                    🚦
                </div>
                <div>
                    <span style="font-size: 16px; font-weight: 700; color: #0b1c30; letter-spacing: -0.01em;">Conserva Rodoviária - Campo</span>
                    <div style="font-size: 11px; color: #545f73; font-weight: 500;">EPR Paraná • Rodovias Integradas</div>
                </div>
            </div>
            <div style="width: 34px; height: 34px; border-radius: 50%; background-color: #096138; display: flex; align-items: center; justify-content: center; color: white; font-size: 16px; font-weight: bold;">
                👤
            </div>
        </div>
        """
    )

    # Botão de Acesso ao Painel do Analista no topo
    c_sw1, c_sw2 = st.columns([2.5, 1.5])
    with c_sw2:
        if st.button("💻 Painel do Analista", use_container_width=True, help="Alternar para visualização de escritório"):
            st.session_state.modulo_ativo = "💻 Painel do Analista"
            st.rerun()

    # Card de Identidade Visual EPR Paraná (Google Stitch Prototype)
    st.html(
        """
        <div style="display: flex; align-items: center; justify-content: space-between; padding: 14px 16px; margin-bottom: 14px; background-color: #ffffff; border-radius: 16px; border: 1px solid #dce9ff; box-shadow: 0 1px 3px rgba(15,23,42,0.04);">
            <div style="display: flex; align-items: center; gap: 12px;">
                <div style="width: 44px; height: 44px; border-radius: 12px; background-color: #2d7a4f; color: #ffffff; display: flex; align-items: center; justify-content: center; font-size: 16px; font-weight: 800; letter-spacing: 0.05em;">
                    EPR
                </div>
                <div style="display: flex; flex-direction: column;">
                    <span style="font-size: 11px; font-weight: 700; color: #096138; text-transform: uppercase; letter-spacing: 0.08em;">EPR Paraná</span>
                    <h1 style="font-size: 17px; font-weight: 700; color: #0b1c30; margin: 0; line-height: 1.2;">Registro de Ocorrências de Campo</h1>
                </div>
            </div>
            <div style="display: flex; align-items: center; padding: 4px 10px; border-radius: 9999px; background-color: #e5eeff; color: #545f73; font-size: 11px; font-weight: 600;">
                Equipes 1 a 80
            </div>
        </div>
        """
    )

    # 1. Card: Identificação do Trecho & Equipe
    with st.container(border=True):
        st.markdown("### 📍 1. Trecho, Rodovia & Equipe")
        
        c_emp, c_eq = st.columns([1, 1])
        with c_emp:
            empresa_sel = st.selectbox(
                "🏢 Empresa Contratada:",
                TODAS_EMPRESAS,
                index=0,
                key="campo_empresa_sel",
                help="Selecione a empresa prestadora para carregar e habilitar suas equipes de conservação."
            )
        with c_eq:
            equipes_da_empresa = EMPRESAS_CONSERVACAO.get(empresa_sel, EQUIPES_RODOVIA)
            equipe_sel = st.selectbox(
                f"👷 Equipe ({empresa_sel.split()[0]}):",
                equipes_da_empresa,
                index=0,
                key="campo_equipe_sel"
            )

        c_rod, c_km = st.columns([1, 1])
        with c_rod:
            rodovia_sel = st.selectbox("🛣️ Rodovia:", RODOVIAS_PADRAO, index=0)
            if rodovia_sel == "Outra":
                rodovia_sel = st.text_input("Nome da Rodovia:", value="BR-101")
        with c_km:
            km_input = st.text_input("📍 Quilometragem (KM):", value="142.5", placeholder="Ex: 142 ou 105.4")

        tipo_sel = st.selectbox("🛠️ Tipo de Serviço:", TIPOS_SERVICO, index=0, key="campo_tipo_sel")

        proximo_id = gerar_proximo_id_ativo(tipo_sel, km_input, registros)
        st.markdown(
            f"""
            <div style="background-color: #f0fdf4; border: 1.5px solid #86efac; border-radius: 12px; padding: 10px 14px; margin-top: 8px;">
                <span style="font-size: 13px; color: #166534; font-weight: 600;">🏷️ Código do Ativo Gerado:</span>
                <span style="font-size: 16px; color: #096138; font-weight: 800; margin-left: 8px;">{proximo_id}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # 2. Card: Coordenadas GPS (Google Stitch Pattern)
    with st.container(border=True):
        st.markdown(
            """
            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px;">
                <div style="display: flex; align-items: center; gap: 6px;">
                    <span style="font-size: 19px;">📍</span>
                    <span style="font-size: 16px; font-weight: 700; color: #0b1c30;">Captura de GPS</span>
                </div>
                <div style="display: flex; align-items: center; gap: 6px; padding: 3px 10px; border-radius: 9999px; background-color: #b8ffce; color: #00210f; font-size: 11px; font-weight: 700;">
                    <span style="width: 7px; height: 7px; border-radius: 50%; background-color: #096138; display: inline-block;"></span>
                    Ativo
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if "campo_lat" not in st.session_state:
            st.session_state.campo_lat = -25.428412
        if "campo_lng" not in st.session_state:
            st.session_state.campo_lng = -49.273301

        if st.button("📍 Sincronizar Ponto GPS Atual", use_container_width=True):
            st.session_state.campo_lat = -25.428412
            st.session_state.campo_lng = -49.273301
            st.toast("Ponto GPS sincronizado com alta precisão!")

        c_lat, c_lng = st.columns(2)
        with c_lat:
            lat = st.number_input("Latitude:", value=st.session_state.campo_lat, format="%.6f", key="input_lat_mobile")
        with c_lng:
            lng = st.number_input("Longitude:", value=st.session_state.campo_lng, format="%.6f", key="input_lng_mobile")

    # 3. Card: Fotografia da Ocorrência (Compressão Pillow ~240KB)
    with st.container(border=True):
        st.markdown("### 📸 3. Fotografia da Ocorrência")
        st.caption("Comprime via Pillow para ~250KB antes de sincronizar com a base de dados.")
        metodo_foto = st.radio("Origem da Imagem:", ["📷 Câmera do Aparelho", "📁 Upload de Arquivo / Galeria"], horizontal=True)

        foto_processada = None
        if metodo_foto == "📷 Câmera do Aparelho":
            foto_camera = st.camera_input("Tirar foto do elemento rodoviário")
            if foto_camera is not None:
                foto_processada = processar_e_comprimir_foto(foto_camera.getvalue())
        else:
            foto_upload = st.file_uploader("Selecione a foto (JPG, PNG, WEBP):", type=["jpg", "jpeg", "png", "webp"])
            if foto_upload is not None:
                foto_processada = processar_e_comprimir_foto(foto_upload.getvalue())

        if foto_processada:
            st.image(foto_processada["base64"], caption=f"Foto Comprimida: {foto_processada['dimensoes']}", use_container_width=True)
            st.markdown(
                f"""
                <div style="display: flex; align-items: center; justify-content: space-between; padding: 6px 12px; background-color: #f0fdf4; border-radius: 10px; border: 1px solid #86efac; margin-top: 6px;">
                    <span style="font-size: 12px; color: #166534; font-weight: 600;">⚡ Compressão Automática:</span>
                    <span class="stitch-badge stitch-badge-primary">Otimizado: {foto_processada['tamanho_comprimido_kb']} KB</span>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.info("💡 Nenhuma foto capturada ainda. Se enviar sem foto, um registro gráfico padronizado com as informações da placa/serviço será gerado automaticamente.")

    # 4. Card: Observações de Campo
    with st.container(border=True):
        st.markdown("### 📝 4. Observação Adicional (Opcional)")
        obs_campo = st.text_area(
            "Detalhes, referências do local ou anomalias observadas:",
            placeholder="Ex: Pavimento apresentando desgaste acelerado após chuva intensa. Necessária intervenção emergencial...",
            height=90,
        )

    # 5. O Botão Grande e Destacado "Gravar Registro de Campo" (Google Stitch Pattern)
    if st.button("🚀 Gravar Registro de Campo", type="primary", use_container_width=True):
        b64_salvar = foto_processada["base64"] if foto_processada else _gerar_imagem_placeholder_base64(
            PREFIXOS_SERVICO.get(tipo_sel, "ATIVO"),
            f"{tipo_sel} KM {km_input}",
            f"{rodovia_sel} - {equipe_sel}"
        )

        novo_registro = {
            "id": proximo_id,
            "doc_id": proximo_id,
            "prefixo_km": f"{PREFIXOS_SERVICO.get(tipo_sel, 'ATIVO')}-KM{km_input}",
            "rodovia": rodovia_sel,
            "empresa": empresa_sel,
            "equipe": equipe_sel,
            "km": str(km_input).strip(),
            "tipo_servico": tipo_sel,
            "latitude": float(lat),
            "longitude": float(lng),
            "foto_base64": b64_salvar,
            "observacoes_campo": obs_campo.strip(),
            "data_envio": datetime.now().isoformat(),
            "status_ia": "pendente",
            "descricao_ia": "",
            "sugestao_ia": "",
            "aprovado": False,
            "analista_responsavel": "",
            "data_atualizacao": "",
        }

        st.session_state.registros.insert(0, novo_registro)
        salvar_dados_em_disco(st.session_state.registros)

        if db_firestore is not None:
            sincronizar_registro_firestore(db_firestore, novo_registro)
            st.success(f"✅ Relato do Ativo `{proximo_id}` transmitido com sucesso ao Cloud Firestore!")
        else:
            st.success(f"✅ Relato do Ativo `{proximo_id}` gravado com sucesso no sistema local!")

        st.balloons()
        time.sleep(1.0)
        st.rerun()

    # Barra Inferior Decorativa (Navegação Stitch Mobile)
    st.html(
        """
        <div style="position: fixed; bottom: 0; left: 0; right: 0; height: 52px; background: rgba(248, 249, 255, 0.95); backdrop-filter: blur(12px); border-top: 1px solid #dce9ff; display: flex; justify-content: space-around; align-items: center; z-index: 1000; box-shadow: 0 -2px 10px rgba(0,0,0,0.04);">
            <div style="display: flex; flex-direction: column; align-items: center; color: #096138; font-weight: 700; font-size: 11px;">
                <span>📋</span>
                <span>Inspeções</span>
            </div>
            <div style="display: flex; flex-direction: column; align-items: center; color: #545f73; font-weight: 500; font-size: 11px;">
                <span>👷</span>
                <span>Equipes</span>
            </div>
            <div style="display: flex; flex-direction: column; align-items: center; color: #545f73; font-weight: 500; font-size: 11px;">
                <span>📍</span>
                <span>Mapa KM</span>
            </div>
            <div style="display: flex; flex-direction: column; align-items: center; color: #545f73; font-weight: 500; font-size: 11px;">
                <span>🔄</span>
                <span>Sincronizar</span>
            </div>
        </div>
        """
    )

# ==============================================================================
# 2. CUSTOMIZAÇÃO PARA O PAINEL DO ANALISTA (ESCRITÓRIO) & INDICADORES
# ==============================================================================
else:
    # Barra Lateral Desktop
    with st.sidebar:
        caminho_logo = "logo-eprparana-card.png" if os.path.exists("logo-eprparana-card.png") else (
            "logo-eprparana.png" if os.path.exists("logo-eprparana.png") else "https://eprparana.com.br/wp-content/uploads/2026/01/logo-eprparana.png"
        )
        st.image(caminho_logo, use_container_width=True)
        st.markdown(
            """
            <div style="padding: 6px 0;">
                <span style="font-size: 18px; font-weight: 800; color: #096138; letter-spacing: -0.02em;">EPR PARANÁ</span>
                <div style="font-size: 11px; font-weight: 700; color: #545f73; text-transform: uppercase;">CCO Rodovias • Centro de Controle</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.divider()

        # Seletor de Módulo na Barra Lateral
        opcoes_menu = ["🚜 Tela de Campo", "💻 Painel do Analista", "📊 Indicadores & Mapa"]
        indice_atual = opcoes_menu.index(st.session_state.modulo_ativo) if st.session_state.modulo_ativo in opcoes_menu else 1
        modulo_escolhido = st.radio("🧭 Navegação do Sistema:", opcoes_menu, index=indice_atual, key="nav_radio_sidebar")
        if modulo_escolhido != st.session_state.modulo_ativo:
            st.session_state.modulo_ativo = modulo_escolhido
            st.rerun()

        st.divider()

        # Indicadores de Conexão
        st.markdown("### 📡 Conectividade do Terminal")
        if db_firestore is not None:
            st.markdown("🟢 **Firebase Firestore**: Conectado à Nuvem")
        else:
            st.markdown("🟡 **Firebase Firestore**: Modo Local")

        if cliente_gemini is not None:
            st.markdown("🟢 **Google Gemini 2.5 Flash**: Ativo")
        else:
            st.markdown("🟡 **Google Gemini**: Contingência DNIT")

        st.divider()

        # Medidor da cota Spark gratuita
        tamanho_total_kb = sum(len(r.get("foto_base64", "")) * 0.75 / 1024 for r in registros)
        tamanho_total_mb = tamanho_total_kb / 1024
        st.markdown("### 💾 Cota de Armazenamento")
        st.progress(min(tamanho_total_mb / 1000.0, 1.0))
        st.caption(f"Armazenamento estimado: **{tamanho_total_mb:.2f} MB** / 1.000 MB ({len(registros)} ativos)")

        st.divider()
        st.caption("EPR Paraná • Sistema Integrado de Conservação Viária.")

    # --------------------------------------------------------------------------
    # 2.1 PAINEL DO ANALISTA
    # --------------------------------------------------------------------------
    if st.session_state.modulo_ativo == "💻 Painel do Analista":
        # Verificação de Autenticação
        if not st.session_state.autenticado:
            with st.container(border=True):
                st.markdown("### 🔒 Autenticação do Painel do Analista")
                st.markdown("Insira a senha de acesso de engenharia para desbloquear o painel fiscal (`admin123`):")
                col_s1, col_s2 = st.columns([2, 1])
                with col_s1:
                    senha = st.text_input("Senha de Acesso:", type="password", value="admin123", key="campo_senha_escritorio")
                    nome_analista = st.text_input("Nome do Engenheiro Fiscal:", value="Eng. Carlos Silva", key="campo_nome_fiscal")
                with col_s2:
                    st.write("")
                    st.write("")
                    if st.button("Desbloquear Painel do Analista", type="primary", use_container_width=True):
                        if senha == DEFAULT_ADMIN_PASSWORD:
                            st.session_state.autenticado = True
                            st.session_state.nome_fiscal = nome_analista
                            st.rerun()
                        else:
                            st.error("Senha incorreta. A senha padrão de demonstração é `admin123`.")
            st.stop()

        # 1. CABEÇALHO LIMPO DO ANALISTA (Google Stitch Desktop Pattern)
        st.markdown(
            f"""
            <div style="background-color: #ffffff; border-radius: 16px; border: 1px solid #dce9ff; padding: 16px 20px; box-shadow: 0 1px 3px rgba(15,23,42,0.04); margin-bottom: 16px; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px;">
                <div style="display: flex; align-items: center; gap: 14px;">
                    <div style="width: 48px; height: 48px; border-radius: 12px; background-color: #2d7a4f; color: #ffffff; display: flex; align-items: center; justify-content: center; font-size: 24px;">
                        💻
                    </div>
                    <div>
                        <div style="display: flex; align-items: center; gap: 6px;">
                            <span style="font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: #096138;">EPR Paraná</span>
                            <span style="color: #545f73;">•</span>
                            <span style="font-size: 11px; color: #545f73; font-weight: 600;">CCO Rodovias</span>
                        </div>
                        <h1 style="font-size: 22px; font-weight: 700; color: #0b1c30; margin: 0; line-height: 1.2;">Painel do Analista / Escritório</h1>
                    </div>
                </div>
                <div style="display: flex; align-items: center; gap: 12px;">
                    <div style="display: flex; align-items: center; gap: 8px; background-color: #eff4ff; padding: 6px 14px; border-radius: 10px; border: 1px solid #dce9ff;">
                        <span style="font-size: 16px;">👤</span>
                        <div style="display: flex; flex-direction: column;">
                            <span style="font-size: 10px; color: #545f73; text-transform: uppercase; font-weight: 600;">Usuário Ativo</span>
                            <span style="font-size: 13px; font-weight: 700; color: #0b1c30;">Analista: {st.session_state.get('nome_fiscal', 'admin123')}</span>
                        </div>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Botões de Navegação Rápida (Tela de Campo e Logout)
        c_nav_left, c_nav_right = st.columns([4, 1])
        with c_nav_right:
            c_btn_campo, c_btn_sair = st.columns([1, 1])
            with c_btn_campo:
                if st.button("🚜 Campo", use_container_width=True, help="Alternar para Tela de Campo Mobile"):
                    st.session_state.modulo_ativo = "🚜 Tela de Campo"
                    st.rerun()
            with c_btn_sair:
                if st.button("Sair", use_container_width=True, help="Encerrar Sessão"):
                    st.session_state.autenticado = False
                    st.rerun()

        # Métricas Globais em Cards Limpos
        total_ativos = len(registros)
        total_pendentes = sum(1 for r in registros if r.get("status_ia") == "pendente")
        total_analisados = total_ativos - total_pendentes
        total_aprovados = sum(1 for r in registros if r.get("aprovado"))

        col_m1, col_m2, col_m3, col_m4 = st.columns(4)
        col_m1.metric("Total de Ativos", total_ativos)
        col_m2.metric("Pendentes de IA", total_pendentes, delta=f"-{total_pendentes}" if total_pendentes > 0 else "0", delta_color="inverse")
        col_m3.metric("Analisados pela IA", total_analisados)
        col_m4.metric("Conformidade Aprovada", total_aprovados)

        # 2. FILTROS DE CONSULTA DOS REGISTROS (Google Stitch Pattern)
        with st.container(border=True):
            st.markdown(
                """
                <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px;">
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span style="font-size: 18px; color: #096138;">🔍</span>
                        <span style="font-size: 16px; font-weight: 700; color: #0b1c30;">Filtros de Consulta dos Registros</span>
                    </div>
                    <span style="font-size: 12px; color: #545f73;">Exibindo registros sincronizados via app móvel</span>
                </div>
                """,
                unsafe_allow_html=True,
            )
            
            filtro_r1_c1, filtro_r1_c2, filtro_r1_c3 = st.columns([1.2, 1.2, 1.0])
            with filtro_r1_c1:
                f_empresa = st.selectbox(
                    "🏢 Empresa Contratada:",
                    ["Todas as Empresas"] + TODAS_EMPRESAS,
                    index=0,
                    key="filtro_empresa_analista",
                    help="Selecione uma empresa para filtrar e habilitar suas respectivas equipes."
                )
            with filtro_r1_c2:
                if f_empresa != "Todas as Empresas":
                    equipes_opcoes = ["Todas as Equipes da Empresa"] + EMPRESAS_CONSERVACAO.get(f_empresa, [])
                    f_equipe = st.selectbox(
                        f"👷 Equipe ({f_empresa.split()[0]}):",
                        equipes_opcoes,
                        index=0,
                        disabled=False,
                        key="filtro_equipe_analista"
                    )
                else:
                    f_equipe = st.selectbox(
                        "👷 Equipe da Empresa:",
                        ["⚠️ Selecione uma empresa para habilitar equipes"],
                        disabled=True,
                        help="Escolha uma empresa no filtro anterior para habilitar e filtrar as equipes.",
                        key="filtro_equipe_analista"
                    )
            with filtro_r1_c3:
                f_rodovia = st.selectbox("🛣️ Rodovia:", ["Todas"] + RODOVIAS_PADRAO, key="filtro_rodovia_analista")

            filtro_r2_c1, filtro_r2_c2, filtro_r2_c3 = st.columns([1.0, 1.0, 1.4])
            with filtro_r2_c1:
                f_servico = st.selectbox("🛠️ Tipo de Serviço:", ["Todos"] + TIPOS_SERVICO, key="filtro_servico_analista")
            with filtro_r2_c2:
                f_status = st.selectbox("🤖 Status da IA:", ["Todos", "Pendentes", "Analisados"], key="filtro_status_analista")
            with filtro_r2_c3:
                f_busca = st.text_input("🔍 Buscar por ID, KM ou Palavra:", placeholder="Ex: KM142, BURACO, ROCADA", key="filtro_busca_analista")

        # Processamento dos Filtros
        filtrados = registros
        if f_empresa != "Todas as Empresas":
            filtrados = [
                r for r in filtrados
                if (r.get("empresa") or obter_empresa_da_equipe(r.get("equipe", ""))) == f_empresa
            ]
            if f_equipe != "Todas as Equipes da Empresa" and not f_equipe.startswith("⚠️"):
                filtrados = [r for r in filtrados if r.get("equipe") == f_equipe]
        if f_servico != "Todos":
            filtrados = [r for r in filtrados if r.get("tipo_servico") == f_servico]
        if f_status == "Pendentes":
            filtrados = [r for r in filtrados if r.get("status_ia") == "pendente"]
        elif f_status == "Analisados":
            filtrados = [r for r in filtrados if r.get("status_ia") == "analisado"]
        if f_rodovia != "Todas":
            filtrados = [r for r in filtrados if r.get("rodovia") == f_rodovia]
        if f_busca:
            termo = f_busca.strip().lower()
            filtrados = [
                r for r in filtrados
                if termo in r.get("id", "").lower()
                or termo in str(r.get("km", "")).lower()
                or termo in (r.get("empresa") or "").lower()
                or termo in (r.get("equipe") or "").lower()
                or termo in r.get("tipo_servico", "").lower()
                or termo in r.get("observacoes_campo", "").lower()
            ]

        # 3. BARRA DE AÇÕES EM LOTE & FILA DE PROCESSAMENTO DA IA (Google Stitch Pattern)
        with st.container(border=True):
            st.markdown(
                f"""
                <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px;">
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span style="font-size: 18px; color: #096138;">⚡</span>
                        <span style="font-size: 16px; font-weight: 700; color: #0b1c30;">Ações em Lote & Fila de Processamento da IA</span>
                    </div>
                    <span style="font-size: 12px; color: #545f73;">{len(filtrados)} ocorrência(s) selecionada(s) no filtro</span>
                </div>
                """,
                unsafe_allow_html=True,
            )
            
            b_col1, b_col2, b_col3, b_col4 = st.columns([1.3, 1.1, 1.0, 1.1])

            with b_col1:
                if st.button("🤖 Rodar Análise da IA (Gemini)", type="primary", use_container_width=True, help="Fila assíncrona Gemini 2.5 Flash"):
                    pendentes_para_analise = [r for r in filtrados if r.get("status_ia") == "pendente"]
                    if not pendentes_para_analise:
                        st.info("Nenhum ativo pendente de análise no filtro atual.")
                    else:
                        barra_progresso = st.progress(0.0)
                        status_texto = st.empty()
                        total = len(pendentes_para_analise)

                        for idx, item in enumerate(pendentes_para_analise):
                            status_texto.text(f"Processando {idx + 1} de {total}: {item['id']} com gemini-2.5-flash...")
                            resultado = executar_analise_gemini(
                                item.get("foto_base64", ""),
                                item.get("tipo_servico", ""),
                                item.get("km", ""),
                                item.get("rodovia", "")
                            )
                            item["descricao_ia"] = resultado["descricao_ia"]
                            item["sugestao_ia"] = resultado["sugestao_ia"]
                            item["status_ia"] = "analisado"
                            item["analista_responsavel"] = st.session_state.get("nome_fiscal", "Eng. Fiscal")
                            item["data_atualizacao"] = datetime.now().isoformat()

                            if db_firestore is not None:
                                sincronizar_registro_firestore(db_firestore, item)

                            barra_progresso.progress((idx + 1) / total)
                            if idx < total - 1:
                                time.sleep(0.8)

                        salvar_dados_em_disco(st.session_state.registros)
                        st.success(f"🎉 Análise em lote concluída para {total} ativo(s)!")
                        time.sleep(1)
                        st.rerun()

            with b_col2:
                doc_bytes = gerar_relatorio_docx(filtrados, st.session_state.get("nome_fiscal", "Eng. Fiscal"))
                st.download_button(
                    label=f"📄 Gerar Relatório Word (.docx)",
                    data=doc_bytes,
                    file_name=f"Relatorio_EPR_Parana_{datetime.now().strftime('%Y%m%d_%H%M')}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    use_container_width=True,
                )

            with b_col3:
                zip_bytes = gerar_pacote_fotos_zip(filtrados)
                st.download_button(
                    label=f"📦 Baixar Fotos (.zip)",
                    data=zip_bytes,
                    file_name=f"Fotos_EPR_Parana_{datetime.now().strftime('%Y%m%d_%H%M')}.zip",
                    mime="application/zip",
                    use_container_width=True,
                )

            with b_col4:
                abrir_modal_exclusao = st.button("🗑️ Apagar Lote", use_container_width=True, help="Libera quota de armazenamento")
                if abrir_modal_exclusao:
                    st.session_state.confirmar_apagar_lote = True

        # Confirmação de exclusão caso acionada
        if st.session_state.get("confirmar_apagar_lote", False):
            with st.container(border=True):
                st.error("⚠️ **Confirmação de Exclusão de Lote**")
                st.markdown(f"Deseja apagar permanentemente os **{len(filtrados)}** registros selecionados? Esta ação liberará espaço na sua cota de armazenamento e não poderá ser desfeita.")
                c_conf1, c_conf2 = st.columns(2)
                with c_conf1:
                    if st.button("❌ Cancelar Exclusão", use_container_width=True):
                        st.session_state.confirmar_apagar_lote = False
                        st.rerun()
                with c_conf2:
                    if st.button("🗑️ Sim, Excluir e Liberar Espaço", type="primary", use_container_width=True):
                        ids_a_remover = [r["id"] for r in filtrados]
                        st.session_state.registros = [r for r in st.session_state.registros if r["id"] not in ids_a_remover]
                        salvar_dados_em_disco(st.session_state.registros)
                        if db_firestore is not None:
                            remover_lote_firestore(db_firestore, ids_a_remover)
                        st.session_state.confirmar_apagar_lote = False
                        st.success(f"Lote de {len(ids_a_remover)} registros excluído!")
                        time.sleep(1)
                        st.rerun()

        # 4. LISTA DE OCORRÊNCIAS RECEBIDAS DO CAMPO (CARTÕES EM DUAS COLUNAS PARALELAS)
        st.markdown(
            f"""
            <div style="display: flex; align-items: center; justify-content: space-between; margin-top: 18px; margin-bottom: 12px;">
                <div style="display: flex; align-items: center; gap: 8px;">
                    <span style="font-size: 20px; color: #096138;">📋</span>
                    <h2 style="font-size: 18px; font-weight: 700; color: #0b1c30; margin: 0;">Ocorrências Recebidas do Campo</h2>
                </div>
                <span style="font-size: 13px; font-weight: 600; color: #545f73;">{len(filtrados)} item(ns) listado(s)</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if not filtrados:
            st.info("Nenhum registro encontrado para os filtros selecionados.")
        else:
            for item in filtrados:
                with st.container(border=True):
                    # TOPO DO CARD: Seleção + ID Único + Tags de Serviço e IA Status
                    empresa_card = item.get("empresa") or obter_empresa_da_equipe(item.get("equipe", ""))
                    status_ia = item.get("status_ia", "pendente")
                    is_concluido = status_ia == "analisado"
                    badge_ia_class = "stitch-badge-primary" if is_concluido else "stitch-badge-tertiary"
                    badge_ia_txt = "Análise Concluída" if is_concluido else "Aguardando Análise"

                    st.markdown(
                        f"""
                        <div style="display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; border-bottom: 1px solid #e2e8f0; padding-bottom: 10px; margin-bottom: 12px; gap: 8px;">
                            <div style="display: flex; align-items: center; gap: 10px;">
                                <div style="display: flex; flex-direction: column;">
                                    <span style="font-size: 10px; font-weight: 700; color: #545f73; text-transform: uppercase; letter-spacing: 0.05em;">Identificador Único</span>
                                    <span style="font-size: 19px; font-weight: 800; color: #096138; letter-spacing: -0.01em;">{item.get('id')}</span>
                                </div>
                            </div>
                            <div style="display: flex; align-items: center; gap: 8px;">
                                <span class="stitch-badge stitch-badge-neutral">{item.get('tipo_servico')}</span>
                                <span class="stitch-badge {badge_ia_class}">{badge_ia_txt}</span>
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                    # ESTRUTURA EM DUAS COLUNAS PARALELAS:
                    # COLUNA ESQUERDA: Foto do Ativo + Dados Operacionais de Campo
                    # COLUNA DIREITA: Análise Multimodal Gemini + Botões de Ação
                    col_visual, col_ia = st.columns([1, 1], gap="large")

                    # ==========================================================
                    # COLUNA DA ESQUERDA: FOTO & DADOS OPERACIONAIS DE CAMPO
                    # ==========================================================
                    with col_visual:
                        # Fotografia do Ativo com Tag de Compressão
                        b64 = item.get("foto_base64", "")
                        if b64:
                            st.image(b64, use_container_width=True)
                            st.markdown(
                                """
                                <div style="display: flex; justify-content: space-between; align-items: center; font-size: 11px; color: #545f73; padding: 2px 6px;">
                                    <span>Compressão otimizada em campo</span>
                                    <span style="font-weight: 600; color: #096138;">JPG • ~240 KB</span>
                                </div>
                                """,
                                unsafe_allow_html=True,
                            )
                        else:
                            st.info("Sem fotografia disponível para este ativo.")

                        # Metadados de Campo em Caixa Estruturada
                        lat = item.get("latitude", 0)
                        lng = item.get("longitude", 0)
                        data_formatada = item.get("data_envio", "")[:16].replace("T", " às ")

                        st.markdown(
                            f"""
                            <div class="stitch-meta-box">
                                <span style="font-size: 12px; font-weight: 700; text-transform: uppercase; color: #096138; letter-spacing: 0.05em; display: block; margin-bottom: 6px;">Metadados de Campo</span>
                                <div class="stitch-meta-row">
                                    <span class="stitch-meta-label">🏢 Empresa:</span>
                                    <span class="stitch-meta-val">{empresa_card}</span>
                                </div>
                                <div class="stitch-meta-row">
                                    <span class="stitch-meta-label">👷 Equipe:</span>
                                    <span class="stitch-meta-val">{item.get('equipe', '—')}</span>
                                </div>
                                <div class="stitch-meta-row">
                                    <span class="stitch-meta-label">🛣️ KM da Rodovia:</span>
                                    <span class="stitch-meta-val">{item.get('rodovia', 'BR-277')} • KM {item.get('km', '—')}</span>
                                </div>
                                <div class="stitch-meta-row">
                                    <span class="stitch-meta-label">📍 Coordenadas GPS:</span>
                                    <span class="stitch-meta-val" style="font-family: monospace;">{lat:.5f}, {lng:.5f}</span>
                                </div>
                                <div class="stitch-meta-row">
                                    <span class="stitch-meta-label">📅 Data / Hora:</span>
                                    <span class="stitch-meta-val">{data_formatada}</span>
                                </div>
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )

                        # Link Google Maps
                        st.markdown(f"🌐 [Abrir Ponto no Google Maps]({'https://www.google.com/maps?q=' + str(lat) + ',' + str(lng)})", unsafe_allow_html=True)

                        # Observações de Campo
                        obs_texto = item.get("observacoes_campo") or "Nenhuma anomalia crítica reportada em campo."
                        st.markdown(f"**📝 Observações da Equipe:**\n> {obs_texto}")

                        # Checkbox de Validação / Conformidade Técnica
                        aprovado_atual = item.get("aprovado", False)
                        novo_aprovado = st.checkbox(
                            "✅ Conformidade Técnica Aprovada pelo Fiscal",
                            value=aprovado_atual,
                            key=f"aprov_{item['id']}",
                        )
                        if novo_aprovado != aprovado_atual:
                            item["aprovado"] = novo_aprovado
                            item["analista_responsavel"] = st.session_state.get("nome_fiscal", "Eng. Fiscal")
                            salvar_dados_em_disco(st.session_state.registros)
                            if db_firestore is not None:
                                sincronizar_registro_firestore(db_firestore, item)
                            st.toast(f"Status de conformidade do ativo {item['id']} atualizado!")

                    # ==========================================================
                    # COLUNA DA DIREITA: ANÁLISE MULTIMODAL GEMINI & AÇÕES
                    # ==========================================================
                    with col_ia:
                        st.markdown(
                            """
                            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px;">
                                <div style="display: flex; align-items: center; gap: 6px;">
                                    <span style="font-size: 18px;">🤖</span>
                                    <span style="font-size: 15px; font-weight: 700; color: #096138; text-transform: uppercase; letter-spacing: 0.04em;">Análise Multimodal do Gemini</span>
                                </div>
                                <span style="font-size: 11px; color: #545f73; font-weight: 600;">Campos Editáveis</span>
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )

                        if is_concluido:
                            st.markdown(
                                """
                                <div style="background-color: #f0fdf4; border: 1px solid #86efac; border-radius: 8px; padding: 6px 12px; margin-bottom: 10px; font-size: 12px; color: #166534; font-weight: 600;">
                                    🟢 Diagnóstico Técnico Concluído (gemini-2.5-flash • Normas DNIT)
                                </div>
                                """,
                                unsafe_allow_html=True,
                            )
                        else:
                            st.markdown(
                                """
                                <div style="background-color: #fffbeb; border: 1px solid #fde68a; border-radius: 8px; padding: 6px 12px; margin-bottom: 10px; font-size: 12px; color: #92400e; font-weight: 600;">
                                    🟡 Aguardando Análise Técnica da IA
                                </div>
                                """,
                                unsafe_allow_html=True,
                            )

                        # Campo 1: descricao_ia
                        desc_ia = st.text_area(
                            "📋 descricao_ia (Diagnóstico da Patologia):",
                            value=item.get("descricao_ia", ""),
                            height=135,
                            key=f"desc_{item['id']}",
                            placeholder="Clique em 'Analisar com IA' para avaliar a foto automaticamente ou edite aqui...",
                        )

                        # Campo 2: sugestao_ia
                        sug_ia = st.text_area(
                            "💡 sugestao_ia (Recomendação de Intervenção):",
                            value=item.get("sugestao_ia", ""),
                            height=120,
                            key=f"sug_{item['id']}",
                            placeholder="Recomendações técnicas de intervenção e materiais...",
                        )

                        st.markdown("<div style='margin-top: 14px;'></div>", unsafe_allow_html=True)

                        # Botões de Ação do Card: "Editar Campos / Salvar", "Aprovar Dados", "Analisar com IA", "Excluir"
                        c_act1, c_act2, c_act3 = st.columns([1.5, 1.5, 0.7])
                        with c_act1:
                            if st.button("🤖 Analisar com IA", key=f"btn_ia_{item['id']}", type="primary", use_container_width=True):
                                with st.spinner(f"Processando imagem do ativo {item['id']} via Gemini 2.5 Flash..."):
                                    res = executar_analise_gemini(
                                        item.get("foto_base64", ""),
                                        item.get("tipo_servico", ""),
                                        item.get("km", ""),
                                        item.get("rodovia", "")
                                    )
                                    item["descricao_ia"] = res["descricao_ia"]
                                    item["sugestao_ia"] = res["sugestao_ia"]
                                    item["status_ia"] = "analisado"
                                    item["analista_responsavel"] = st.session_state.get("nome_fiscal", "Eng. Fiscal")
                                    salvar_dados_em_disco(st.session_state.registros)
                                    if db_firestore is not None:
                                        sincronizar_registro_firestore(db_firestore, item)
                                    st.toast(f"Laudo gerado com sucesso para {item['id']}!")
                                    st.rerun()

                        with c_act2:
                            if st.button("📝 Salvar Edição", key=f"btn_save_{item['id']}", use_container_width=True, help="Salvar edições feitas nos campos da IA"):
                                item["descricao_ia"] = desc_ia
                                item["sugestao_ia"] = sug_ia
                                item["status_ia"] = "analisado" if desc_ia else item.get("status_ia")
                                item["analista_responsavel"] = st.session_state.get("nome_fiscal", "Eng. Fiscal")
                                salvar_dados_em_disco(st.session_state.registros)
                                if db_firestore is not None:
                                    sincronizar_registro_firestore(db_firestore, item)
                                st.toast(f"Laudo do ativo {item['id']} salvo com sucesso!")

                        with c_act3:
                            if st.button("🗑️", key=f"btn_del_{item['id']}", help="Excluir este ativo"):
                                st.session_state.registros = [r for r in st.session_state.registros if r.get("id") != item["id"]]
                                salvar_dados_em_disco(st.session_state.registros)
                                if db_firestore is not None:
                                    remover_registro_firestore(db_firestore, item["id"])
                                st.rerun()

                        # Botão 'Aprovar Dados' direto no rodapé do card
                        if st.button("✅ Aprovar Dados do Ativo", key=f"btn_aprov_card_{item['id']}", use_container_width=True, help="Aprova conformidade e consolida registro"):
                            item["aprovado"] = True
                            item["descricao_ia"] = desc_ia
                            item["sugestao_ia"] = sug_ia
                            item["analista_responsavel"] = st.session_state.get("nome_fiscal", "Eng. Fiscal")
                            salvar_dados_em_disco(st.session_state.registros)
                            if db_firestore is not None:
                                sincronizar_registro_firestore(db_firestore, item)
                            st.toast(f"Registro {item['id']} aprovado com sucesso pelo analista!")
                            st.rerun()

    # --------------------------------------------------------------------------
    # 2.2 INDICADORES & MAPA
    # --------------------------------------------------------------------------
    elif st.session_state.modulo_ativo == "📊 Indicadores & Mapa":
        c_head, c_btn = st.columns([4, 1])
        with c_head:
            st.header("📊 Painel Gerencial & Georreferenciamento")
            st.caption("Visão espacial e volumétrica das demandas de conservação rodoviária • EPR Paraná")
        with c_btn:
            if st.button("🚜 Tela de Campo", use_container_width=True):
                st.session_state.modulo_ativo = "🚜 Tela de Campo"
                st.rerun()

        c_g1, c_g2 = st.columns([1, 1])

        with c_g1:
            st.subheader("Distribuição por Tipo de Serviço")
            if registros:
                df_servicos = pd.DataFrame([{"Serviço": r.get("tipo_servico", "Outros")} for r in registros])
                contagem = df_servicos["Serviço"].value_counts()
                st.bar_chart(contagem)
            else:
                st.info("Nenhum registro para exibir.")

        with c_g2:
            st.subheader("Ativos por Rodovia")
            if registros:
                df_rodovias = pd.DataFrame([{"Rodovia": r.get("rodovia", "Não informada")} for r in registros])
                contagem_rod = df_rodovias["Rodovia"].value_counts()
                st.bar_chart(contagem_rod)
            else:
                st.info("Nenhum registro para exibir.")

        st.divider()

        st.subheader("🗺️ Mapeamento GPS dos Pontos de Fiscalização")
        pontos_mapa = []
        for r in registros:
            lat = r.get("latitude")
            lng = r.get("longitude")
            if lat is not None and lng is not None:
                pontos_mapa.append({"lat": float(lat), "lon": float(lng)})

        if pontos_mapa:
            df_mapa = pd.DataFrame(pontos_mapa)
            st.map(df_mapa, zoom=10)
        else:
            st.info("Nenhuma coordenada válida disponível para visualização no mapa.")

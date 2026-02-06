import streamlit as st
import pandas as pd
import fitz  # PyMuPDF
import re
import io
import pytesseract
from pdf2image import convert_from_bytes
from datetime import datetime
from PIL import Image

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="Extrator PGFN 100%", page_icon="⭐", layout="wide")

# --- FUNÇÕES ---

def parse_currency(value_str):
    if not value_str: return 0.0
    try:
        # Limpa sujeira e padroniza
        clean = str(value_str).replace(" ", "").replace("R$", "")
        clean = re.sub(r'[^\d,\.]', '', clean)
        clean = clean.replace(".", "").replace(",", ".")
        return float(clean)
    except:
        return 0.0

def encontrar_saldo_blindado(text):
    """
    Busca o saldo com Prioridade Absoluta para o padrão SISPAR (Sonora).
    """
    # Lista de padrões em ordem de PRECEDÊNCIA (O primeiro que achar válido, leva)
    patterns = [
        # 1. Padrão Ouro SISPAR: "Saldo Devedor com Juros" (Pega mesmo com quebra de linha ou abrev.)
        # Ex: "Saldo Devedor com Juros: 100.000,00"
        (r"Saldo\s*Devedor\s*c(?:om|/)\s*Juros.*?(?:R\$)?\s*([\d\.]+,\d{2})", "Saldo Devedor c/ Juros"),
        
        # 2. Padrão Regularize: "Valor total consolidado" (Rodapé azul)
        (r"Valor\s*total\s*consolidado.*?(?:R\$)?\s*([\d\.]+,\d{2})", "Vlr Total Consolidado"),
        
        # 3. Padrão Genérico Forte: "Total Geral"
        (r"Total\s*Geral.*?(?:R\$)?\s*([\d\.]+,\d{2})", "Total Geral"),
        
        # 4. Fallbacks para tabelas OCR e outros
        (r"Total:.*?(?:R\$)?\s*([\d\.]+,\d{2})", "Total (Tabela)"),
        (r"(?:Saldo\s*Devedor|Valor\s*Consolidado).*?(?:R\$)?\s*([\d\.]+,\d{2})", "Saldo/Consolidado Genérico"),
        
        # 5. Último recurso: Procura "Total" no fim da linha com valor grande
        (r"Total.*?(?:R\$)?.*?([\d\.]+,\d{2})", "Total OCR") 
    ]
    
    # Varre os padrões. Se achar um valor > 0 no padrão de alta prioridade, retorna na hora.
    for pat, nome in patterns:
        matches = re.findall(pat, text, re.IGNORECASE | re.DOTALL)
        # Filtra matches válidos
        valores_validos = []
        for m in matches:
            v = parse_currency(m)
            if v > 100: # Ignora valores irrisórios/lixo
                valores_validos.append(v)
        
        if valores_validos:
            # Retorna o maior valor encontrado DENTRO deste padrão prioritário
            return max(valores_validos), nome
            
    return 0.0, "Não encontrado"

def extrair_identificadores_completos(text):
    identificadores = []
    
    # Busca Negociação (Aceita "Negociação" ou "Negoc")
    match_neg = re.search(r"(?:Número da Negociação|Negociaç[ãa]o)[:\s№º\.]*(\d{1,15})(?!\d)", text, re.IGNORECASE)
    negociacao = match_neg.group(1) if match_neg else None

    # Busca Inscrições (Padrão 11 7 11...)
    inscricoes = re.findall(r"(\d{2}\s*\d\s*\d{2}\s*\d{6}[-\s]\d{2})", text)
    inscricoes = sorted(list(set([i.replace("\n", " ").strip() for i in inscricoes])))
    
    partes = []
    if negociacao: partes.append(f"Negoc: {negociacao}")
    
    if inscricoes:
        lista_str = ", ".join(inscricoes[:3])
        if len(inscricoes) > 3: lista_str += "..."
        partes.append(f"Insc: {lista_str}")
    
    if not partes:
        match_gen = re.search(r"(?:Conta|Parcelamento).*?[:\.]\s*(\d+)", text, re.IGNORECASE)
        if match_gen: partes.append(f"ID: {match_gen.group(1)}")
        else: return "Desconhecido", "-"

    return " | ".join(partes), "Composto"

def inferir_modalidade(text, raw_modalidade=""):
    if raw_modalidade:
        raw_modalidade = raw_modalidade.replace("\n", " ").strip()
        raw_modalidade = re.split(r"(?:Data|Situa|Valor|N[º°])", raw_modalidade, flags=re.IGNORECASE)[0]
        raw_modalidade = re.sub(r"^\d{5,}.*?-\s*", "", raw_modalidade) 
    
    if len(raw_modalidade) < 5 or "TIPO DE" in raw_modalidade.upper():
        raw_modalidade = ""
    
    if len(raw_modalidade) > 10: return raw_modalidade.strip()

    upper = text.upper()
    mapa = {
        "EC 113": "Parcelamento EC 113",
        "EC113": "Parcelamento EC 113",
        "13.485": "PERT (Lei 13.485)",
        "TRANSACAO EXCEPCIONAL": "Transação Excepcional",
        "EXTRAORDINARIA": "Transação Extraordinária",
        "DIVIDA ATIVA": "Dívida Ativa",
        "SIMPLES NACIONAL": "Simples Nacional",
        "SISPAR": "Parcelamento SISPAR",
        "PREVIDENCIARIO": "Previdenciário (Geral)"
    }
    for key, val in mapa.items():
        if key in upper: return val
    return "Não Identificada"

def extrair_modalidade_multilinha(text):
    stop_words = r"(?:Situa|Data|Valor|N[º°]|Inscri|Natureza|Receita|Quant)"
    match_mod = re.search(r"Modalidade[:\s\.]*(.*?)(?=\n\s*" + stop_words + r"|$)", text, re.IGNORECASE | re.DOTALL)
    if match_mod: return match_mod.group(1).strip()
    match_rec = re.search(r"Receita da dívida[:\s\.]*(.*?)(?=\n\s*" + stop_words + r"|$)", text, re.IGNORECASE | re.DOTALL)
    if match_rec: return match_rec.group(1).strip()
    return ""

# --- ENGINE OCR ---
def aplicar_ocr(pdf_bytes):
    try:
        images = convert_from_bytes(pdf_bytes, dpi=300)
        full_text = ""
        for img in images:
            text = pytesseract.image_to_string(img, lang='por')
            full_text += text + "\n"
        return full_text
    except: return ""

def processar(uploaded_file):
    filename = uploaded_file.name
    metodo = "Texto Nativo"
    pdf_bytes = uploaded_file.read()
    
    full_text = ""
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for page in doc: full_text += page.get_text() + "\n"
    except: pass

    if len(full_text.strip()) < 50:
        metodo = "OCR (Imagem)"
        with st.status(f"Processando {filename}...", expanded=True):
            full_text = aplicar_ocr(pdf_bytes)
    
    identificador, tipo_id = extrair_identificadores_completos(full_text)
    saldo, metodo_saldo = encontrar_saldo_blindado(full_text)
    
    raw_mod = extrair_modalidade_multilinha(full_text)
    modalidade_final = inferir_modalidade(full_text, raw_mod)
    
    return {
        "Arquivo": filename,
        "Identificador": identificador,
        "Modalidade": modalidade_final,
        "Saldo (R$)": saldo,
        "Método Leitura": metodo
    }

# --- INTERFACE ---
st.title("⭐ Extrator PGFN 10.0 (Final)")
st.markdown("Extração calibrada para priorizar 'Saldo Devedor com Juros' e Negociações.")

arquivos = st.file_uploader("Arraste seus PDFs", type=["pdf"], accept_multiple_files=True)

if arquivos:
    if st.button("Processar"):
        dados = []
        bar = st.progress(0)
        
        for i, arq in enumerate(arquivos):
            res = processar(arq)
            dados.append(res)
            bar.progress((i+1)/len(arquivos))
            
        df = pd.DataFrame(dados)
        st.success("Pronto!")
        
        st.dataframe(df.style.format({"Saldo (R$)": "R$ {:,.2f}"}), use_container_width=True)
        st.metric("Total", f"R$ {df['Saldo (R$)'].sum():,.2f}")
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False)
            ws = writer.sheets['Sheet1']
            ws.set_column('B:B', 50)
            ws.set_column('C:C', 40)
            ws.set_column('D:D', 18)
            
        st.download_button("Baixar Excel", buffer.getvalue(), f"PGFN_Final_{datetime.now().strftime('%H%M')}.xlsx")

import streamlit as st
import pandas as pd
import fitz  # PyMuPDF
import re
import io
from datetime import datetime

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="Extrator de Saldos PGFN", page_icon="💰", layout="wide")

# --- FUNÇÕES ---

def parse_currency(value_str):
    """Converte '1.234,56' para float 1234.56"""
    if not value_str: return 0.0
    try:
        clean = str(value_str).replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
        return float(clean)
    except:
        return 0.0

def extrair_saldo_focado(uploaded_file):
    filename = uploaded_file.name
    saldo = 0.0
    identificador = "Não identificado"
    tipo_extrato = "Desconhecido"

    try:
        # Lê apenas a 1ª página
        with fitz.open(stream=uploaded_file.read(), filetype="pdf") as doc:
            if len(doc) < 1:
                return {"Arquivo": filename, "Identificador": "-", "Saldo (R$)": 0.0}
            
            # Extrai texto preservando layout físico aproximado
            text = doc[0].get_text()
            
            # --- ESTRATÉGIA 1: Layout SISPAR (Consulta de Negociações) ---
            # Busca: "Saldo Devedor com Juros:"
            # Regex explicada: Procura a frase, ignora : e espaços, pega números, pontos e vírgulas
            match_sispar = re.search(r"Saldo Devedor com Juros:?\s*([\d\.,]+)", text, re.IGNORECASE)
            
            if match_sispar:
                saldo = parse_currency(match_sispar.group(1))
                tipo_extrato = "Sispar (Negociação)"
                # Tenta pegar o número da negociação para identificar
                match_id = re.search(r"Número da Negociação:?\s*(\d+)", text, re.IGNORECASE)
                if match_id: identificador = match_id.group(1)

            # --- ESTRATÉGIA 2: Layout REGULARIZE (Relatório Detalhado) ---
            # Busca: "Valor total consolidado" (geralmente no rodapé azul)
            else:
                # Regex mais flexível para pegar o valor que aparece após o texto, mesmo com quebras de linha
                match_regularize = re.search(r"Valor total consolidado.*?R\$\s*([\d\.,]+)", text, re.IGNORECASE | re.DOTALL)
                
                if match_regularize:
                    saldo = parse_currency(match_regularize.group(1))
                    tipo_extrato = "Regularize (Inscrição)"
                    # Tenta pegar o número da inscrição
                    match_id = re.search(r"N[º°]\s*inscrição:?\s*([\d\s\.]+)", text, re.IGNORECASE)
                    if match_id: identificador = match_id.group(1).strip()
            
            # --- ESTRATÉGIA 3 (Fallback): Tenta achar qualquer "Valor Consolidado" ---
            if saldo == 0.0:
                match_fallback = re.search(r"Valor Consolidado:?\s*([\d\.,]+)", text, re.IGNORECASE)
                if match_fallback:
                    saldo = parse_currency(match_fallback.group(1))
                    tipo_extrato = "Genérico"

    except Exception as e:
        tipo_extrato = "Erro de Leitura"

    return {
        "Arquivo": filename,
        "Identificador (Insc/Negoc)": identificador,
        "Tipo": tipo_extrato,
        "Saldo do Extrato": saldo
    }

# --- INTERFACE ---

st.title("💰 Extrator de Saldos de Parcelamento")
st.markdown("Focado exclusivamente em extrair o **Saldo Devedor / Valor Consolidado** da primeira página.")

arquivos = st.file_uploader("Arraste os PDFs aqui", type=["pdf"], accept_multiple_files=True)

if arquivos:
    if st.button("Extrair Saldos"):
        with st.spinner("Analisando valores..."):
            dados = []
            prog = st.progress(0)
            
            for i, arq in enumerate(arquivos):
                resultado = extrair_saldo_focado(arq)
                dados.append(resultado)
                prog.progress((i + 1) / len(arquivos))
            
            df = pd.DataFrame(dados)
            
            # Exibição
            st.success("Concluído!")
            
            # Formata a coluna de saldo para visualização
            st.dataframe(
                df.style.format({"Saldo do Extrato": "R$ {:,.2f}"}), 
                use_container_width=True
            )
            
            # Métrica Total
            total = df["Saldo do Extrato"].sum()
            st.metric("Soma Total dos Saldos", f"R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

            # Download
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False, sheet_name="Saldos")
                # Ajuste de largura e formato moeda no Excel
                workbook = writer.book
                worksheet = writer.sheets["Saldos"]
                fmt_money = workbook.add_format({'num_format': '#,##0.00'})
                worksheet.set_column('D:D', 20, fmt_money) # Coluna de Saldo
                worksheet.set_column('A:B', 25)
            
            st.download_button(
                label="⬇️ Baixar Excel",
                data=buffer.getvalue(),
                file_name=f"Saldos_PGFN_{datetime.now().strftime('%H%M')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

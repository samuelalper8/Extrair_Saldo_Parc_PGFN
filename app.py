import streamlit as st
import pandas as pd
import fitz  # PyMuPDF
import re
import io
from datetime import datetime

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="Extrator Universal PGFN", page_icon="🚜", layout="wide")

# --- FUNÇÕES DE AJUDA ---

def parse_currency(value_str):
    """Transforma strings numéricas BR (1.000,00) em float (1000.00)."""
    if not value_str: return 0.0
    try:
        # Remove R$, espaços e caracteres invisíveis
        clean = re.sub(r'[^\d,\.]', '', str(value_str))
        # Remove pontos de milhar e troca vírgula decimal por ponto
        clean = clean.replace(".", "").replace(",", ".")
        return float(clean)
    except:
        return 0.0

def encontrar_melhor_saldo(text):
    """
    Tenta encontrar o saldo final usando várias estratégias de regex,
    ordenadas por confiabilidade (do mais específico para o mais genérico).
    """
    val = 0.0
    metodo = "Não encontrado"
    
    # Lista de padrões (Regex) e seus pesos/confiabilidade
    # O padrão procura a chave e pega o valor monetário que estiver na mesma linha ou logo depois
    patterns = [
        # Estratégia 1: SISPAR / Parcelamentos Especiais (Saldo Devedor com Juros é o que importa)
        (r"Saldo Devedor com Juros.*?(?:R\$)?\s*([\d\.]+,\d{2})", "Saldo Devedor c/ Juros"),
        
        # Estratégia 2: Regularize / Extratos Detalhados (Valor Total Consolidado)
        (r"Valor total consolidado.*?(?:R\$)?\s*([\d\.]+,\d{2})", "Vlr Total Consolidado"),
        
        # Estratégia 3: Tabelas de Parcelamento (Linha de Totais no rodapé)
        # Procura "Total:" seguido de valor no final da linha
        (r"\bTotal:.*?(?:R\$)?\s*([\d\.]+,\d{2})", "Total (Tabela)"),
        
        # Estratégia 4: EC 113 ou Transações (Saldo Devedor Total ou Consolidado)
        (r"(?:Saldo Devedor|Valor Consolidado).*?(?:R\$)?\s*([\d\.]+,\d{2})", "Saldo/Consolidado Genérico"),
        
        # Estratégia 5: Fallback para tabelas simples onde aparece apenas "Total"
        (r"\bTotal\b.*?(?:R\$)?\s*([\d\.]+,\d{2})", "Total Simples")
    ]

    for pat, nome_metodo in patterns:
        # re.IGNORECASE | re.DOTALL permite que o valor esteja na linha de baixo em alguns casos
        matches = re.findall(pat, text, re.IGNORECASE | re.DOTALL)
        if matches:
            # Pega o último match encontrado (geralmente o total está no final do documento)
            # ou o match que tiver o maior valor (heurística para evitar pegar parcelas)
            valores = [parse_currency(m) for m in matches]
            # Filtra zeros
            valores = [v for v in valores if v > 0]
            
            if valores:
                # Assume o maior valor encontrado nesse padrão como o saldo (evita pegar valor de parcela)
                melhor_valor = max(valores)
                return melhor_valor, nome_metodo

    return 0.0, "Não identificado"

def extrair_identificador(text):
    """Tenta identificar Inscrição, Negociação ou Processo."""
    # 1. Negociação (Comum em EC 113 e Sispar)
    match_neg = re.search(r"(?:Negociação|Conta|Parcelamento)[:\s№º]*(\d+)", text, re.IGNORECASE)
    if match_neg: return match_neg.group(1), "Negociação"
    
    # 2. Inscrição (Comum em Dívida Ativa)
    match_insc = re.search(r"Inscrição[:\s№º]*([\d\s\.\/-]+)", text, re.IGNORECASE)
    if match_insc: return match_insc.group(1).strip(), "Inscrição"
    
    return "Desconhecido", "-"

def processar_pdf_universal(uploaded_file):
    filename = uploaded_file.name
    full_text = ""
    
    try:
        # Lê o PDF
        with fitz.open(stream=uploaded_file.read(), filetype="pdf") as doc:
            # Estratégia: Ler todas as páginas, pois em extratos EC 113 o total pode estar na pág 2 ou 3
            for page in doc:
                full_text += page.get_text() + "\n"
        
        # 1. Extrair Identificador
        identificador, tipo_id = extrair_identificador(full_text)
        
        # 2. Extrair Saldo (Motor Inteligente)
        saldo, metodo = encontrar_melhor_saldo(full_text)
        
        # 3. Identificar Tipo de Extrato (apenas para referência)
        tipo_doc = "Genérico"
        if "EC 113" in full_text or "EC113" in full_text: tipo_doc = "EC 113"
        elif "TRANSAÇÃO" in full_text.upper(): tipo_doc = "Transação"
        elif "13.485" in full_text: tipo_doc = "Lei 13.485"
        elif "REGULARIZE" in full_text.upper(): tipo_doc = "Regularize"

    except Exception as e:
        return {
            "Arquivo": filename,
            "Tipo Doc": "Erro",
            "Identificador": f"Erro: {str(e)}",
            "Saldo (R$)": 0.0,
            "Método": "Falha Leitura"
        }

    return {
        "Arquivo": filename,
        "Tipo Doc": tipo_doc,
        "Identificador": identificador,
        "Saldo (R$)": saldo,
        "Método": metodo
    }

# --- INTERFACE ---

st.title("🚜 Extrator Universal de Parcelamentos (PGFN)")
st.markdown("""
**Versão 3.0 (Blindada)** - Projetada para ler:
* ✅ EC 113
* ✅ Transação Excepcional
* ✅ Lei 13.485
* ✅ Regularize Comum
""")

arquivos = st.file_uploader("Arraste TODOS os PDFs (Misturados)", type=["pdf"], accept_multiple_files=True)

if arquivos:
    if st.button("Extrair Dados"):
        with st.spinner("Escaneando documentos..."):
            dados = []
            prog = st.progress(0)
            
            for i, arq in enumerate(arquivos):
                res = processar_pdf_universal(arq)
                dados.append(res)
                prog.progress((i + 1) / len(arquivos))
            
            df = pd.DataFrame(dados)
            
            st.success("Extração Concluída!")
            
            # Formatação visual
            st.dataframe(
                df.style.format({"Saldo (R$)": "R$ {:,.2f}"}), 
                use_container_width=True
            )
            
            # Total
            total = df["Saldo (R$)"].sum()
            col1, col2 = st.columns(2)
            col1.metric("Total dos Saldos", f"R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
            col2.info("Verifique a coluna 'Método' para confirmar como o valor foi encontrado.")

            # Download
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False, sheet_name="Saldos")
                wb = writer.book
                ws = writer.sheets["Saldos"]
                fmt = wb.add_format({'num_format': '#,##0.00'})
                ws.set_column('D:D', 18, fmt)
                ws.set_column('A:A', 30)
                ws.set_column('C:C', 20)
            
            st.download_button("⬇️ Baixar Excel", buffer.getvalue(), f"Saldos_V3_{datetime.now().strftime('%H%M')}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

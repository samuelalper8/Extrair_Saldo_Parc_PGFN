import streamlit as st
import pandas as pd
import fitz  # PyMuPDF
import re
import io
import json
import urllib.request
import unicodedata
from datetime import datetime

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="Extrator Avançado PGFN/RFB", page_icon="🕵️", layout="wide")

# ================= 1. HELPERS E CONSULTA CNPJ (Portados do Módulo) =================

_CNPJ_LOOKUP_CACHE = {}

def _cnpj_digits(s: str) -> str:
    return re.sub(r"\D", "", str(s or ""))[:14]

def _mask_cnpj_digits(s: str) -> str:
    d = _cnpj_digits(s)
    if len(d) != 14: return s or ""
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:14]}"

def _cnpj_lookup_online(cnpj_in: str) -> str:
    """Consulta CNPJ na BrasilAPI (lógica original)."""
    try:
        d = _cnpj_digits(cnpj_in)
        if len(d) != 14: return ""
        if d in _CNPJ_LOOKUP_CACHE: return _CNPJ_LOOKUP_CACHE[d]
        
        # Timeout curto para não travar o Streamlit
        req = urllib.request.Request(
            f"https://brasilapi.com.br/api/cnpj/v1/{d}",
            headers={"User-Agent": "conprev-streamlit"}
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
                nome = (data.get("razao_social") or data.get("nome_fantasia") or "").strip()
                if nome:
                    _CNPJ_LOOKUP_CACHE[d] = nome
                    return nome
        return ""
    except:
        return ""

def _resolve_name_prefer_cnpj(label: str, cnpj_masked: str) -> str:
    # Tenta resolver o nome pelo CNPJ online, senão usa o label extraído do PDF
    nm = _cnpj_lookup_online(cnpj_masked)
    return nm or (label or "")

def parse_currency(value_str):
    try:
        clean = str(value_str).replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
        return float(clean)
    except:
        return 0.0

# ================= 2. LÓGICA DE EXTRAÇÃO AVANÇADA (PyMuPDF) =================

def _fallback_scan_processo_fiscal(lines, itens, filename, current_org=None, current_cnpj=None):
    """Varredura secundária para Processos Fiscais que falharam na blocagem."""
    proc_re = re.compile(r'(?:^|\b)(\d{4,6}\.\d{3}\.\d{3}/\d{4}-\d{2})(?:\b|$)')
    seen = set(x.get("Processo/Cod") for x in itens if x.get("Tipo") == "PROCESSO FISCAL")
    NEG_BLACKLIST = ("AJUIZ", "NEGOCIAD", "SUSPENS", "JULG", "MANIFESTA", "AJUIZAVEL", "IMPUGNAC", "CREDITO", "SISPAR")

    for idx, raw in enumerate(lines):
        m = proc_re.search(raw or "")
        if not m: continue
        proc = m.group(1)
        if proc in seen: continue

        # Janela de contexto
        prev_l = (lines[idx-1] if idx-1 >= 0 else "") or ""
        next_l = (lines[idx+1] if idx+1 < len(lines) else "") or ""
        janela = (prev_l + " " + raw + " " + next_l).upper()

        if "DEVEDOR" not in janela: continue
        if re.search(r'\bDEVEDOR\b-', janela): continue # "DEVEDOR -" costuma ser lixo
        if any(tok in janela for tok in NEG_BLACKLIST): continue
        
        # Evita confundir CNPJ com Processo
        if len(re.sub(r'\D', '', proc)) == 14: continue

        cnpj_disp = _mask_cnpj_digits(current_cnpj) if current_cnpj else ""
        org_disp = _resolve_name_prefer_cnpj(current_org or "", cnpj_disp)

        itens.append({
            "Arquivo": filename,
            "Tipo": "PROCESSO FISCAL (Fallback)",
            "Órgão/Contribuinte": org_disp,
            "CNPJ": cnpj_disp,
            "Processo/Cod": proc,
            "Descrição/Nome": "Processo em Situação DEVEDOR",
            "Competência": "-",
            "Vencimento": "-",
            "Valor Original": 0.0,
            "Valor Consolidado": 0.0,
            "Situação": "DEVEDOR"
        })
        seen.add(proc)

def extrair_pdf_complexo(uploaded_file):
    """
    Adaptação da função _extract_itens_pdf do arquivo original.
    Lê o PDF bloco a bloco usando PyMuPDF.
    """
    itens = []
    filename = uploaded_file.name
    
    # Abre o PDF da memória
    try:
        doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
    except Exception as e:
        return [{"Arquivo": filename, "Tipo": "ERRO", "Descrição/Nome": str(e)}]

    current_cnpj = None
    current_org = None
    
    # Mapa de cabeçalho para tentar vincular CNPJ a Órgão
    header_map = {}
    full_text_cache = ""
    for page in doc:
        full_text_cache += page.get_text() + "\n"
    
    # Regex simples para popular mapa de cabeçalho
    for m in re.finditer(r"CNPJ:\s*([\d\./\-]{14,20}).{0,160}?vinculado.*?\n([^\n]+)", full_text_cache, flags=re.I):
        cn = re.sub(r"\D", "", m.group(1))[:14]
        header_map[cn] = " ".join(m.group(2).split())

    for page in doc:
        pf_inside = False
        pf_prev_proc = None
        
        # Extrai estrutura de blocos/linhas
        d = page.get_text("dict")
        lines = []
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                text = "".join(span.get("text", "") for span in line.get("spans", ""))
                text = " ".join(text.split())
                if text:
                    lines.append(text)
        
        i = 0
        while i < len(lines):
            t = lines[i]
            U = t.upper()

            # 1. Identificação de CNPJ/Órgão corrente
            if "CNPJ" in U:
                m = re.search(r"CNPJ[:\s]*([0-9\.\-\/]{14,18})(?:\s*-\s*(.+))?", t, flags=re.I)
                if m:
                    current_cnpj = re.sub(r"\D", "", m.group(1))
                    name_inline = (m.group(2) or "").strip()
                    if name_inline and not re.search(r"\d", name_inline):
                        current_org = name_inline
                    else:
                        # Busca nome nas próximas linhas (heurística do script original)
                        for j in range(1, 5):
                            if i + j >= len(lines): break
                            nxt = lines[i+j].strip()
                            if len(nxt) >= 5 and not re.search(r"\d", nxt) and "PÁGINA" not in nxt.upper():
                                current_org = nxt
                                break
                i += 1
                continue

            # 2. DEVEDOR (Lógica posicional baseada no script original)
            if U == "DEVEDOR":
                # Verifica se não estamos dentro de um bloco de processo fiscal
                if pf_inside:
                    i += 1; continue
                
                try:
                    # O script original olha "para trás" para pegar os valores
                    # Layout esperado: Cod - Nome | Comp | Venc | Orig | Dev | Multa | Juros | Cons | DEVEDOR
                    cod_nome = lines[i-8]
                    comp = lines[i-7]
                    venc = lines[i-6]
                    val_orig = lines[i-5]
                    # ... pular intermediários se necessário, foco no Consolidado (i-1)
                    val_cons = lines[i-1]
                    
                    if " - " in cod_nome:
                        cod, nome = cod_nome.split(" - ", 1)
                    else:
                        cod, nome = cod_nome.split(" ", 1) if " " in cod_nome else (cod_nome, "")

                    cnpj_disp = _mask_cnpj_digits(current_cnpj)
                    org_disp = _resolve_name_prefer_cnpj(current_org, cnpj_disp)

                    itens.append({
                        "Arquivo": filename,
                        "Tipo": "DEVEDOR",
                        "Órgão/Contribuinte": org_disp,
                        "CNPJ": cnpj_disp,
                        "Processo/Cod": cod.strip(),
                        "Descrição/Nome": nome.strip(),
                        "Competência": comp.strip(),
                        "Vencimento": venc.strip(),
                        "Valor Original": parse_currency(val_orig),
                        "Valor Consolidado": parse_currency(val_cons),
                        "Situação": "DEVEDOR"
                    })
                except:
                    # Se falhar a lógica posicional, pega linha crua
                    itens.append({"Arquivo": filename, "Tipo": "DEVEDOR (Layout irregular)", "Descrição/Nome": t})
                i += 1
                continue

            # 3. MAED (Multa por Atraso)
            if "MAED" in U:
                try:
                    # Layout esperado: MAED... | Comp | Venc | Orig | Dev | Situação
                    comp = lines[i+1] # Ou PA (Período Apuração)
                    venc = lines[i+2]
                    val_orig = lines[i+3]
                    situacao = lines[i+5]
                    
                    parts = t.split(" - ")
                    cod = parts[0].strip()
                    desc = parts[1].strip() if len(parts) > 1 else "MAED"
                    
                    cnpj_disp = _mask_cnpj_digits(current_cnpj)
                    
                    itens.append({
                        "Arquivo": filename,
                        "Tipo": "MAED",
                        "Órgão/Contribuinte": _resolve_name_prefer_cnpj(current_org, cnpj_disp),
                        "CNPJ": cnpj_disp,
                        "Processo/Cod": cod,
                        "Descrição/Nome": desc,
                        "Competência": comp,
                        "Vencimento": venc,
                        "Valor Original": parse_currency(val_orig),
                        "Valor Consolidado": parse_currency(val_orig), # Em MAED geralmente é igual
                        "Situação": situacao.strip()
                    })
                except:
                    itens.append({"Arquivo": filename, "Tipo": "MAED (Layout irregular)", "Descrição/Nome": t})
                i += 1
                continue

            # 4. PROCESSO FISCAL (SIEF) - Lógica de Bloco
            if ("PENDÊNCIA - PROCESSO FISCAL" in U) or ("PROCESSO FISCAL (SIEF)" in U):
                pf_inside = True
                i += 1; continue
            
            if ("PENDÊNCIA -" in U) and ("PROCESSO FISCAL" not in U):
                pf_inside = False
            
            if pf_inside:
                # Ignorar headers
                if U.strip() in ("PROCESSO", "SITUAÇÃO", "LOCALIZAÇÃO"):
                    i += 1; continue
                
                # Captura Processo
                m_proc = re.search(r"(?:^|\b)(\d{4,6}\.\d{3}\.\d{3}/\d{4}-\d{2})", t)
                if m_proc:
                    pf_prev_proc = m_proc.group(1)
                    i += 1; continue
                
                # Verifica se é DEVEDOR
                if "DEVEDOR" in U and pf_prev_proc:
                    cnpj_disp = _mask_cnpj_digits(current_cnpj)
                    itens.append({
                        "Arquivo": filename,
                        "Tipo": "PROCESSO FISCAL",
                        "Órgão/Contribuinte": _resolve_name_prefer_cnpj(current_org, cnpj_disp),
                        "CNPJ": cnpj_disp,
                        "Processo/Cod": pf_prev_proc,
                        "Descrição/Nome": "Processo SIEF",
                        "Competência": "-",
                        "Vencimento": "-",
                        "Valor Original": 0.0,
                        "Valor Consolidado": 0.0,
                        "Situação": "DEVEDOR"
                    })
                    pf_prev_proc = None # Consome o processo
                i += 1
                continue

            # 5. OMISSÃO
            if "OMISS" in U and ("OMISSÃO" in U or "OMISSAO" in U):
                # Tenta achar o período na próxima linha
                periodo = "Não identificado"
                months = "JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ"
                for k in range(1, 7):
                    if i + k >= len(lines): break
                    look = lines[i+k].strip().upper()
                    if re.search(rf"\b(19|20)\d{{2}}\b", look) or re.search(rf"\b({months})\b", look):
                        periodo = lines[i+k].strip()
                        break
                
                cnpj_disp = _mask_cnpj_digits(current_cnpj)
                itens.append({
                    "Arquivo": filename,
                    "Tipo": "OMISSÃO",
                    "Órgão/Contribuinte": _resolve_name_prefer_cnpj(current_org, cnpj_disp),
                    "CNPJ": cnpj_disp,
                    "Processo/Cod": "-",
                    "Descrição/Nome": "Omissão de Declaração",
                    "Competência": periodo,
                    "Vencimento": "-",
                    "Valor Original": 0.0,
                    "Valor Consolidado": 0.0,
                    "Situação": "OMISSÃO"
                })
                i += 1
                continue

            i += 1

    # Fallback para processos fiscais que escaparam da lógica de bloco
    _fallback_scan_processo_fiscal(lines, itens, filename, current_org, current_cnpj)
    
    return itens

# ================= 3. INTERFACE STREAMLIT =================

st.title("🔎 Extrator Avançado de Restrições (RFB/PGFN)")
st.markdown("""
Esta ferramenta utiliza a lógica avançada do **Módulo de Restrições**:
* Detecta **DEVEDOR**, **MAED** e **OMISSÃO**.
* Utiliza lógica posicional para capturar valores e vencimentos corretamente.
* Consulta nomes de **CNPJ na BrasilAPI** para melhor identificação.
""")

uploaded_pdfs = st.file_uploader(
    "Carregue os Relatórios de Situação Fiscal (PDF)", 
    type=["pdf"], 
    accept_multiple_files=True
)

if uploaded_pdfs:
    if st.button("🚀 Iniciar Análise Avançada"):
        all_data = []
        progress_bar = st.progress(0)
        
        for idx, pdf_file in enumerate(uploaded_pdfs):
            with st.spinner(f"Processando {pdf_file.name}..."):
                dados_arquivo = extrair_pdf_complexo(pdf_file)
                all_data.extend(dados_arquivo)
            progress_bar.progress((idx + 1) / len(uploaded_pdfs))
        
        if all_data:
            df = pd.DataFrame(all_data)
            
            # Ordenação e Limpeza
            cols_order = [
                "Arquivo", "Tipo", "Órgão/Contribuinte", "CNPJ", 
                "Processo/Cod", "Descrição/Nome", "Competência", 
                "Vencimento", "Valor Original", "Valor Consolidado", "Situação"
            ]
            # Garante que todas colunas existem
            for c in cols_order:
                if c not in df.columns: df[c] = "-"
            
            df = df[cols_order]

            st.success(f"Extração Concluída! {len(all_data)} itens encontrados.")
            
            # Métricas
            col1, col2, col3 = st.columns(3)
            col1.metric("Total Consolidado (R$)", f"{df['Valor Consolidado'].sum():,.2f}")
            col2.metric("Itens DEVEDOR/MAED", len(df[df['Tipo'].isin(['DEVEDOR', 'MAED'])]))
            col3.metric("Omissões", len(df[df['Tipo'] == 'OMISSÃO']))

            st.dataframe(df, use_container_width=True)
            
            # Download Excel
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False, sheet_name='Restricoes')
                # Ajuste largura colunas
                worksheet = writer.sheets['Restricoes']
                worksheet.set_column('A:K', 18)
            
            st.download_button(
                label="⬇️ Baixar Planilha Excel",
                data=output.getvalue(),
                file_name=f"Relatorio_Restricoes_{datetime.now().strftime('%d%m%Y')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            st.warning("Nenhum dado de restrição (DEVEDOR/MAED/OMISSÃO) encontrado nos PDFs.")

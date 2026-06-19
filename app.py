from flask import Flask, render_template, request
import pandas as pd
import json
import io

app = Flask(__name__)

# ==========================================
# PENYIMPANAN SEMENTARA (TEMPORARY STORAGE)
# ==========================================
temp_dashboards = {
    1: None,
    2: None,
    3: None,
    4: None
}

def get_cleaned_data(file_stream):
    content = file_stream.read().decode('utf-8')
    lines = content.splitlines()
    
    header_idx = 0
    for i, line in enumerate(lines):
        # Cari baris yang mengandung NO dan UKE II sebagai indikator header
        if "NO" in line.upper() and "UKE II" in line.upper():
            header_idx = i
            break

    df = pd.read_csv(io.StringIO(content), skiprows=header_idx)
    
    # 1. MAPPING KOLOM DINAMIS
    cols = df.columns.tolist()
    dynamic_mapping = {}
    
    for i, col in enumerate(cols):
        col_str = str(col).strip().upper()
        if col_str in ["NO", "NO.", "NOMOR"]:
            dynamic_mapping[col] = 'NO'
        elif "UKE II" in col_str:
            dynamic_mapping[col] = 'unit_kerja'
        elif "PAGU ANGGARAN" in col_str:
            dynamic_mapping[col] = 'pagu'
        elif "RENCANA PENYERAPAN ANGGARAN" in col_str:
            dynamic_mapping[col] = 'rencana_rp'
            if i + 1 < len(cols):
                dynamic_mapping[cols[i+1]] = 'rencana_pct'
        elif "REALISASI ANGGARAN" in col_str:
            dynamic_mapping[col] = 'realisasi_rp'
            if i + 1 < len(cols):
                dynamic_mapping[cols[i+1]] = 'realisasi_pct'
        elif "REALISASI FISIK" in col_str:
            dynamic_mapping[col] = 'fisik_pct'
        elif "SISA PAGU" in col_str:
            dynamic_mapping[col] = 'sisa_pagu'
        elif "DEVIASI PENYERAPAN" in col_str:
            dynamic_mapping[col] = 'deviasi_pct'
        elif "HAMBATAN" in col_str:
            dynamic_mapping[col] = 'hambatan'
        elif "UPAYA" in col_str or "REKOMENDASI" in col_str:
            dynamic_mapping[col] = 'upaya'

    # Rename kolom terlebih dahulu agar konsisten
    df = df.rename(columns=dynamic_mapping)
    
    # 2. PASTIKAN BARIS TOTAL TERBACA
    if 'NO' in df.columns and 'unit_kerja' in df.columns:
        df['NO'] = df['NO'].astype(str).str.strip().str.upper()
        # Paksa baris yang ber-ID TOTAL untuk memiliki nama unit kerja 'TOTAL'
        df.loc[df['NO'] == 'TOTAL', 'unit_kerja'] = 'TOTAL'

    # 3. PASTIKAN KOLOM ESENSIAL ADA
    needed_cols = ['unit_kerja', 'pagu', 'rencana_rp', 'rencana_pct', 'realisasi_rp', 'realisasi_pct', 'fisik_pct', 'sisa_pagu', 'deviasi_pct', 'hambatan', 'upaya']
    for col in needed_cols:
        if col not in df.columns:
            df[col] = 0 if col.endswith('_rp') or col.endswith('_pct') or col == 'pagu' or col == 'sisa_pagu' else ""

    df_clean = df[needed_cols].copy()
    
    # Buang baris yang tidak ada data Unit Kerjanya
    df_clean = df_clean.dropna(subset=['unit_kerja'])

    # 4. PARSER MATA UANG ROBUST (Handle campuran titik dan koma)
    def clean_curr_robust(x):
        if pd.isna(x): return 0.0
        if isinstance(x, (int, float)): return float(x)
        x = str(x).replace('Rp', '').replace(' ', '').replace('"', '').replace('-', '0').strip()
        if not x: return 0.0
        
        last_dot = x.rfind('.')
        last_comma = x.rfind(',')
        
        if last_dot != -1 and last_comma != -1:
            if last_dot > last_comma:
                x = x.replace(',', '')
            else:
                x = x.replace('.', '').replace(',', '.')
        elif last_dot != -1:
            if len(x) - last_dot - 1 == 3 and x.count('.') > 1:
                x = x.replace('.', '')
            elif len(x) - last_dot - 1 == 3 and x.count('.') == 1:
                x = x.replace('.', '')
            else:
                pass
        elif last_comma != -1:
            if len(x) - last_comma - 1 == 3 and x.count(',') > 1:
                x = x.replace(',', '')
            elif len(x) - last_comma - 1 == 3 and x.count(',') == 1:
                x = x.replace(',', '')
            else:
                x = x.replace(',', '.')
                
        try: return float(x)
        except: return 0.0

    def clean_pct(x):
        if pd.isna(x): return 0.0
        if isinstance(x, (int, float)): return float(x)
        
        # Tambahkan .replace('–', '-') untuk mengubah en-dash Excel ke minus biasa
        x = str(x).replace('–', '-').replace('%', '').replace(' ', '').replace('"', '').strip()
        
        if not x: return 0.0
        x = x.replace(',', '.') 
        try: return float(x)
        except: return 0.0

    def format_raw_text(text):
        text_str = str(text).strip()
        excel_errors = ['#NAME?', '#N/A', '#VALUE!', '#REF!', '#DIV/0!', '#NULL!', '#NUM!']
        if pd.isna(text) or text_str == '' or text_str.lower() == 'nan' or text_str in excel_errors:
            return "Tidak ada catatan."
        return text_str

    for col in ['pagu', 'rencana_rp', 'realisasi_rp', 'sisa_pagu']:
        df_clean[col] = df_clean[col].apply(clean_curr_robust)
        
    for col in ['rencana_pct', 'realisasi_pct', 'fisik_pct', 'deviasi_pct']:
        df_clean[col] = df_clean[col].apply(clean_pct)
        
    df_clean['hambatan'] = df_clean['hambatan'].apply(format_raw_text)
    df_clean['upaya'] = df_clean['upaya'].apply(format_raw_text)

    df_clean['deviasi_rp'] = df_clean['rencana_rp'] - df_clean['realisasi_rp']
    df_clean['deviasi_rp'] = df_clean['deviasi_rp'].apply(lambda x: max(0, x))

    df_clean = df_clean[df_clean['pagu'] > 0]
    
    return df_clean

def prepare_dashboard_data(df, dash_id, title):
    df_units = df[df['unit_kerja'] != 'TOTAL']
    df_total = df[df['unit_kerja'] == 'TOTAL'].iloc[0] if not df[df['unit_kerja'] == 'TOTAL'].empty else None

    chart_data = {
        'labels': df_units['unit_kerja'].tolist(),
        'pagu': df_units['pagu'].tolist(),
        'rencana_rp': df_units['rencana_rp'].tolist(),
        'realisasi_rp': df_units['realisasi_rp'].tolist(),
        'deviasi_rp': df_units['deviasi_rp'].tolist(), 
        'sisa_pagu': df_units['sisa_pagu'].tolist(),
        'rencana_pct': df_units['rencana_pct'].tolist(),
        'realisasi_pct': df_units['realisasi_pct'].tolist(),
        'deviasi_pct': df_units['deviasi_pct'].tolist()
    }
    
    table_data = df_units.to_dict(orient='records')
    
    total_summary = {
        'pagu': df_total['pagu'] if df_total is not None else 0,
        'rencana': df_total['rencana_rp'] if df_total is not None else 0,
        'realisasi': df_total['realisasi_rp'] if df_total is not None else 0,
        'sisa_pagu': df_total['sisa_pagu'] if df_total is not None else 0,
        'fisik': df_total['fisik_pct'] if df_total is not None else 0,
        'deviasi': df_total['deviasi_pct'] if df_total is not None else 0,
        'rencana_pct': df_total['rencana_pct'] if df_total is not None else 0,
        'realisasi_pct': df_total['realisasi_pct'] if df_total is not None else 0
    }

    return {
        'id': dash_id,
        'title': title,
        'chart_data_json': json.dumps(chart_data),
        'table_data_json': json.dumps(table_data),
        'table_data': table_data,
        'total': total_summary
    }

@app.route('/', methods=['GET', 'POST'])
def index():
    errors = []
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action and action.startswith('delete_'):
            slot_to_delete = int(action.split('_')[1])
            temp_dashboards[slot_to_delete] = None
        
        for i in range(1, 5):
            file_key = f'csv_file_{i}'
            title_key = f'title_{i}'
            
            if title_key in request.form:
                new_title = request.form.get(title_key, '').strip()
                if new_title and temp_dashboards[i] is not None:
                    temp_dashboards[i]['title'] = new_title
            
            if file_key in request.files and request.files[file_key].filename != '':
                if action == f'delete_{i}':
                    continue
                    
                try:
                    df = get_cleaned_data(request.files[file_key])
                    dash_title = request.form.get(title_key, '').strip()
                    if not dash_title:
                        dash_title = f'Data UKE I Slot {i}'
                        
                    temp_dashboards[i] = prepare_dashboard_data(df, i, dash_title)
                except Exception as e:
                    errors.append(f"Format file di Slot {i} tidak sesuai/rusak.")

    dashboards = [temp_dashboards[i] for i in range(1, 5) if temp_dashboards[i] is not None]

    grand_total = None
    grand_chart_json = "{}"
    grand_table = []

    if len(dashboards) > 0:
        avg_fisik = sum(d['total']['fisik'] for d in dashboards) / len(dashboards)
        avg_deviasi = sum(d['total']['deviasi'] for d in dashboards) / len(dashboards)
        
        # Hitung total untuk pagu dan realisasi agar bisa mencari persentase yang akurat
        total_pagu = sum(d['total']['pagu'] for d in dashboards)
        total_realisasi = sum(d['total']['realisasi'] for d in dashboards)
        
        # Hitung persentase realisasi grand total
        grand_realisasi_pct = (total_realisasi / total_pagu * 100) if total_pagu > 0 else 0
        
        grand_total = {
            'pagu': total_pagu,
            'rencana': sum(d['total']['rencana'] for d in dashboards),
            'realisasi': total_realisasi,
            'sisa_pagu': sum(d['total']['sisa_pagu'] for d in dashboards),
            'fisik': round(avg_fisik, 2),
            'deviasi': round(avg_deviasi, 2),
            'realisasi_pct': round(grand_realisasi_pct, 2) # Tambahkan baris ini
        }
        
        grand_chart = {
            'labels': [d['title'] for d in dashboards],
            'pagu': [d['total']['pagu'] for d in dashboards],
            'rencana_rp': [d['total']['rencana'] for d in dashboards],
            'realisasi_rp': [d['total']['realisasi'] for d in dashboards],
            'deviasi_rp': [max(0, d['total']['rencana'] - d['total']['realisasi']) for d in dashboards],
            'sisa_pagu': [d['total']['sisa_pagu'] for d in dashboards],
            'rencana_pct': [d['total']['rencana_pct'] for d in dashboards],
            'realisasi_pct': [d['total']['realisasi_pct'] for d in dashboards],
            'deviasi_pct': [d['total']['deviasi'] for d in dashboards]
        }
        grand_chart_json = json.dumps(grand_chart)
        
        for d in dashboards:
            grand_table.append({
                'unit_kerja': d['title'],
                'pagu': d['total']['pagu'],
                'rencana_rp': d['total']['rencana'],
                'rencana_pct': d['total']['rencana_pct'],
                'realisasi_rp': d['total']['realisasi'],
                'realisasi_pct': d['total']['realisasi_pct'],
                'sisa_pagu': d['total']['sisa_pagu'],
                'fisik_pct': d['total']['fisik'],
                'deviasi_pct': d['total']['deviasi']
            })

    return render_template('index.html', 
                           dashboards=dashboards, 
                           slots=temp_dashboards, 
                           errors=errors,
                           grand_total=grand_total,
                           grand_chart_json=grand_chart_json,
                           grand_table=grand_table)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
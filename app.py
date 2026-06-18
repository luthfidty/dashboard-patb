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
        if line.startswith("NO,UKE II"):
            header_idx = i
            break

    df = pd.read_csv(io.StringIO(content), skiprows=header_idx)
    df.loc[df['NO'] == 'TOTAL', 'UKE II '] = 'TOTAL'

    col_mapping = {
        'UKE II ': 'unit_kerja',
        'PAGU ANGGARAN \n(Rp)': 'pagu',
        'RENCANA PENYERAPAN ANGGARAN': 'rencana_rp',
        'Unnamed: 5': 'rencana_pct',
        'REALISASI ANGGARAN': 'realisasi_rp',
        'Unnamed: 7': 'realisasi_pct',
        'REALISASI FISIK \n(%)': 'fisik_pct',
        'SISA PAGU MINGGU INI \n(Rp)': 'sisa_pagu',
        'DEVIASI PENYERAPAN ANGGARAN \n(%)': 'deviasi_pct',
        'HAMBATAN / PERMASALAHAN': 'hambatan',
        'UPAYA / REKOMENDASI PENYELESAIAN': 'upaya'
    }

    df = df.rename(columns=col_mapping)
    df_clean = df[list(col_mapping.values())].copy()
    df_clean = df_clean.dropna(subset=['unit_kerja'])

    def clean_curr(x):
        if isinstance(x, str): x = x.replace(',', '').replace(' ', '').replace('Rp', '').replace('"', '').replace('-', '0')
        try: return float(x)
        except: return 0.0

    def clean_pct(x):
        if isinstance(x, str): x = x.replace('%', '').strip()
        try: return float(x)
        except: return 0.0

    def format_raw_text(text):
        text_str = str(text).strip()
        excel_errors = ['#NAME?', '#N/A', '#VALUE!', '#REF!', '#DIV/0!', '#NULL!', '#NUM!']
        if pd.isna(text) or text_str == '' or text_str in excel_errors:
            return "Tidak ada catatan."
        return text_str

    for col in ['pagu', 'rencana_rp', 'realisasi_rp', 'sisa_pagu']:
        df_clean[col] = df_clean[col].apply(clean_curr)
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
        
        # 1. CEK APAKAH USER MENEKAN TOMBOL "HAPUS"
        if action and action.startswith('delete_'):
            slot_to_delete = int(action.split('_')[1])
            temp_dashboards[slot_to_delete] = None  # Kosongkan data di slot tersebut
        
        # 2. PROSES UPLOAD DAN UPDATE JUDUL
        for i in range(1, 5):
            file_key = f'csv_file_{i}'
            title_key = f'title_{i}'
            
            # Update Judul (Bisa mengubah judul tanpa harus upload ulang)
            if title_key in request.form:
                new_title = request.form.get(title_key, '').strip()
                if new_title and temp_dashboards[i] is not None:
                    temp_dashboards[i]['title'] = new_title
            
            # Proses Jika Ada File Baru yang Diunggah
            if file_key in request.files and request.files[file_key].filename != '':
                # Jangan proses upload di slot yang baru saja tombol hapusnya ditekan
                if action == f'delete_{i}':
                    continue
                    
                try:
                    df = get_cleaned_data(request.files[file_key])
                    dash_title = request.form.get(title_key, '').strip()
                    if not dash_title:
                        dash_title = f'Data UKE I Slot {i}'
                        
                    # Simpan hasil olahan ke memori sementara
                    temp_dashboards[i] = prepare_dashboard_data(df, i, dash_title)
                except Exception as e:
                    errors.append(f"Format file di Slot {i} tidak sesuai/rusak.")

    # 3. KUMPULKAN DASHBOARD YANG AKTIF
    dashboards = [temp_dashboards[i] for i in range(1, 5) if temp_dashboards[i] is not None]

    # 4. HITUNG GRAND TOTAL UNTUK MASTER DASHBOARD
    grand_total = None
    grand_chart_json = "{}"
    grand_table = []

    if len(dashboards) > 0:
        avg_fisik = sum(d['total']['fisik'] for d in dashboards) / len(dashboards)
        avg_deviasi = sum(d['total']['deviasi'] for d in dashboards) / len(dashboards)
        
        grand_total = {
            'pagu': sum(d['total']['pagu'] for d in dashboards),
            'rencana': sum(d['total']['rencana'] for d in dashboards),
            'realisasi': sum(d['total']['realisasi'] for d in dashboards),
            'sisa_pagu': sum(d['total']['sisa_pagu'] for d in dashboards),
            'fisik': round(avg_fisik, 2),
            'deviasi': round(avg_deviasi, 2)
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
                           slots=temp_dashboards, # Melempar status Slot ke HTML
                           errors=errors,
                           grand_total=grand_total,
                           grand_chart_json=grand_chart_json,
                           grand_table=grand_table)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
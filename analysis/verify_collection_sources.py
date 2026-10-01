"""Compare every verified original Excel cell to the embedded CSV source row."""
import argparse,hashlib,io,json,zipfile,math
from pathlib import Path
import pandas as pd
from openpyxl import load_workbook

def normalize(v):
    if v is None:return ''
    if isinstance(v,(int,float)):return format(v,'.15g')
    return str(v)

def run(archive,out):
    rows=0;cells=0;files=0;lineage=[]
    with zipfile.ZipFile(archive) as z:
        plan_name=next(n for n in z.namelist() if n.endswith('장기수집_계획.json'));prefix=plan_name.rsplit('/',1)[0]+'/'
        plan=json.loads(z.read(plan_name))
        for task in plan['tasks']:
            if task['state']!='verified':continue
            receipt=task['receipt'];directory=prefix+receipt['attempt_dir'].replace('\\','/')+'/'
            csv_file=next(f for f in receipt['files'] if f['path'].endswith('.csv'))
            csv=pd.read_csv(io.BytesIO(z.read(directory+csv_file['path'].replace('\\','/'))),encoding='utf-8-sig',keep_default_na=False)
            expected=[json.loads(v) for v in csv['원본행JSON']];offset=0
            for f in receipt['files']:
                if not f['path'].endswith('.xlsx'):continue
                name=directory+f['path'].replace('\\','/');data=z.read(name)
                assert hashlib.sha256(data).hexdigest()==f['sha256']
                wb=load_workbook(io.BytesIO(data),read_only=True,data_only=True);it=iter(wb.active.values);headers=list(next(it))
                for rowno,values in enumerate(it,2):
                    entry=expected[offset]
                    assert headers==entry['headers'],name
                    for actual,stored in zip(values,entry['values']):
                        if isinstance(actual,(int,float)):
                            assert math.isclose(float(actual),float(stored),rel_tol=0,abs_tol=1e-10),(name,rowno,actual,stored)
                        else:
                            assert normalize(actual)==normalize(stored),(name,rowno,actual,stored)
                    lineage.append({'task':task['id'],'profile':task['profile'],'source_csv':directory+csv_file['path'].replace('\\','/'),'source_csv_row':offset+2,'source_excel':name,'source_excel_row':rowno})
                    offset+=1;cells+=len(values);rows+=1
                wb.close();files+=1
            assert offset==len(expected)==receipt['rows']
            if files%100<10:print(f'{files} files / {rows} rows',flush=True)
    assert (rows,cells,files)==(112848,2031264,1251),(rows,cells,files)
    Path(out).mkdir(exist_ok=True,parents=True)
    pd.DataFrame(lineage).to_csv(Path(out)/'source_excel_lineage.csv.gz',index=False,compression='gzip')
    (Path(out)/'source_verification.json').write_text(json.dumps({'excel_files':files,'rows':rows,'cells':cells,'mismatches':0,'numeric_absolute_tolerance':1e-10,'text_identifiers_exact':True},indent=2),encoding='utf-8')
    print('all source cells match',flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--archive',required=True);p.add_argument('--out',required=True);a=p.parse_args();run(a.archive,a.out)

import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from xml.sax.saxutils import escape

SPEC=importlib.util.spec_from_file_location('publication_page', Path(__file__).resolve().parents[1] / 'pages' / '01_게시된_투찰전략.py')
page=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(page)


def fixture(name='일반 용역', rates=('-0.1000%', '참여대상 없음', '참여대상 없음'), lower='88%', formula=False):
    data=[['검토안 / 2000.01.01'],page.HEADERS,[name,lower,*rates]]
    rows=[]
    for ri,row in enumerate(data,1):
        cells=[]
        for ci,text in enumerate(row):
            value='<f>SUM(1,2)</f><v>3</v>' if formula and ri==3 and ci==2 else '<is><t>'+escape(text)+'</t></is>'
            cells.append(f'<c r="{chr(65+ci)}{ri}" t="inlineStr">{value}</c>')
        rows.append(f'<row r="{ri}">'+''.join(cells)+'</row>')
    files={
      'xl/workbook.xml':'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="투찰전략" sheetId="1" r:id="rId1"/></sheets></workbook>',
      'xl/_rels/workbook.xml.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
      'xl/worksheets/sheet1.xml':'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+''.join(rows)+'</sheetData></worksheet>'}
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
        for name,data in files.items(): z.writestr(name,data)
    return buf.getvalue()

class Stopped(Exception): pass
class FakeStreamlit:
    def __init__(self, secret=None, state=None):
        self.secrets={} if secret is None else {'ADMIN_PWD':secret}
        self.session_state={} if state is None else state
        self.errors=[]
    def error(self,x): self.errors.append(x)
    def info(self,x): pass
    def stop(self): raise Stopped()
    def form(self,x): return self
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def text_input(self,*args,**kwargs): pass
    def form_submit_button(self,*args,**kwargs): self.callback=kwargs['on_click']

class PublicationTests(unittest.TestCase):
    def test_valid(self): self.assertEqual(len(page.parse_result(fixture())['summary']),1)
    def test_single_residential(self): self.assertEqual(page.parse_result(fixture('공동주택 전기감리'))['summary'][0]['업체2추천'],'참여대상 없음')
    def test_reject_three_residential(self):
        with self.assertRaises(page.PublicationError): page.parse_result(fixture('주택건설공사 감리자',('-0.2%','0.1%','0.3%')))
    def test_reject_nonzip(self):
        with self.assertRaises(page.PublicationError): page.parse_result(b'notxlsx')
    def test_reject_empty(self):
        with self.assertRaises(page.PublicationError): page.parse_result(b'')
    def test_reject_oversize(self):
        with self.assertRaises(page.PublicationError): page.parse_result(b'x'*(page.MAX_FILE+1))
    def test_reject_summary_formula(self):
        with self.assertRaises(page.PublicationError): page.parse_result(fixture(formula=True))
    def test_reject_unexpected_rate(self):
        with self.assertRaises(page.PublicationError): page.parse_result(fixture(rates=('99.8%','','')))
    def test_zero_rate_kept(self): self.assertEqual(page.parse_result(fixture(rates=('0.0000%','','')))['summary'][0]['업체1추천'],'0.0000%')
    def test_reject_bad_lower(self):
        with self.assertRaises(page.PublicationError): page.parse_result(fixture(lower='101%'))
    def test_missing_filename_date(self):
        with self.assertRaises(page.PublicationError): page.analysis_date_from_name('result.xlsx')
    def test_bad_filename_date(self):
        with self.assertRaises(page.PublicationError): page.analysis_date_from_name('20261340_file.xlsx')
    def test_holds_preserved(self): self.assertIn('보류',page.parse_result(fixture('[참고·자동입력 보류] 통신감리'))['summary'][0]['공고명'])
    def test_atomic_and_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'data'; raw=fixture()
            m,created=page.publish_result(root,'20000101_검토안_v2.xlsx',raw)
            self.assertTrue(created); self.assertFalse((root/'.publish.lock').exists())
            m2,created2=page.publish_result(root,'20000101_검토안_v2.xlsx',raw)
            self.assertFalse(created2); self.assertEqual(m,m2)
            self.assertEqual(page.verify_publication(root/m['id'])[1],raw)
    def test_filename_content_date_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(page.PublicationError): page.publish_result(Path(d),'20000102_검토안.xlsx',fixture())
    def test_concurrent_lock(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'.publish.lock').write_text('owned elsewhere')
            with self.assertRaises(page.PublicationError): page.publish_result(root,'20000101_검토안.xlsx',fixture())
            self.assertEqual((root/'.publish.lock').read_text(),'owned elsewhere')
    def test_modified_file_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); m,_=page.publish_result(root,'20000101_검토안.xlsx',fixture())
            (root/m['id']/'result.xlsx').write_bytes(fixture(rates=('0.2%','','')))
            with self.assertRaises(page.PublicationError): page.verify_publication(root/m['id'])
    def test_recorded_row_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); m,_=page.publish_result(root,'20000101_검토안.xlsx',fixture())
            m['rows']=99; (root/m['id']/'metadata.json').write_text(json.dumps(m))
            with self.assertRaises(page.PublicationError): page.verify_publication(root/m['id'])
    def test_no_password_rejected(self):
        f=FakeStreamlit()
        with self.assertRaises(Stopped): page.authenticate(f)
        self.assertTrue(f.errors)
    def test_default_password_rejected(self):
        with self.assertRaises(Stopped): page.authenticate(FakeStreamlit('admin1234'))
    def test_anonymous_cannot_pass(self):
        with self.assertRaises(Stopped): page.authenticate(FakeStreamlit('test-only-not-a-production-password'))
    def test_wrong_password_does_not_authenticate(self):
        f=FakeStreamlit('test-only-not-a-production-password')
        with self.assertRaises(Stopped): page.authenticate(f)
        f.session_state['publication_password']='wrong'; f.callback()
        self.assertNotIn('publication_auth',f.session_state)
        self.assertNotIn('publication_password',f.session_state)
    def test_password_change_invalidates_session(self):
        f=FakeStreamlit('test-only-password-one')
        with self.assertRaises(Stopped): page.authenticate(f)
        f.session_state['publication_password']='test-only-password-one'; f.callback()
        page.authenticate(f)
        f.secrets['ADMIN_PWD']='test-only-password-two'
        with self.assertRaises(Stopped): page.authenticate(f)

if __name__=='__main__': unittest.main(verbosity=2)

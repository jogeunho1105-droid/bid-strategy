import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error
import pandas as pd
import ledger_store as store
import ledger_metadata


class MemoryStore(store.GitHubStore):
    def __init__(self):
        self.settings = {'branch': 'main'}
        self.private = True
        self.files = {}
        self.writes = []
        self.fail_pointer = False

    def check_private(self):
        if not self.private:
            raise store.StoreError('private required')

    def read(self, name, raw=False, missing=False):
        if name not in self.files:
            if missing:
                return None
            raise store.StoreError('missing')
        content = self.files[name]
        return content if raw else {'size': len(content), 'sha': hashlib.sha256(content).hexdigest(),
                                    'content': base64.b64encode(content).decode()}

    def request(self, suffix='', body=None, **kwargs):
        name = suffix.removeprefix('/contents/')
        if self.fail_pointer and name == 'latest.json':
            raise store.StoreError('concurrent update rejected')
        current = self.read(name, missing=True)
        if current:
            assert body.get('sha') == current['sha']
        self.writes.append(name)
        self.files[name] = base64.b64decode(body['content'])


class PersistenceTests(unittest.TestCase):
    def frame(self, version='test-1'):
        frame = pd.DataFrame({'value': [None, 0.0]})
        frame.attrs[ledger_metadata.ATTR] = {'version': version}
        return frame

    def test_repeat_upload_and_new_version_preserve_old_artifact(self):
        s = MemoryStore();a=s.publish(b'first', self.frame());s.publish(b'first', self.frame())
        self.assertEqual(len(s.writes), 2)
        b=s.publish(b'second', self.frame('test-2'))
        self.assertEqual(s.files[a['path']], b'first')
        self.assertEqual(s.files[b['path']], b'second')
        self.assertEqual(s.pointer()[0]['version'], 'test-2')

    def test_pointer_write_failure_keeps_previous_active_version(self):
        s=MemoryStore();s.publish(b'first', self.frame());s.fail_pointer=True
        with self.assertRaises(store.StoreError):s.publish(b'second', self.frame('test-2'))
        self.assertEqual(s.pointer()[0]['version'], 'test-1')

    def test_missing_local_file_restores_once_and_existing_file_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'history.pkl';frame=self.frame();save=Mock(side_effect=lambda f:path.write_bytes(b'saved'))
            with patch.object(store, 'GitHubStore') as backend:
                backend.return_value.download.return_value=frame
                self.assertTrue(store.restore_if_needed(path,save,{'repository':'private/ledger'}))
                self.assertFalse(store.restore_if_needed(path,save,{'repository':'private/ledger'}))
                save.assert_called_once_with(frame);backend.return_value.download.assert_called_once()

    def test_first_configured_connection_verifies_remote_before_replacing_local_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'history.pkl';path.write_bytes(b'old-local');save=Mock()
            with patch.object(store,'GitHubStore') as backend:
                backend.return_value.download.side_effect=store.StoreError('connection failed')
                with self.assertRaises(store.StoreError):store.restore_if_needed(path,save,{'repository':'private/ledger'})
                save.assert_not_called();self.assertEqual(path.read_bytes(),b'old-local')
                self.assertFalse(Path(str(path)+'.remote-source.json').exists())

    def test_bad_download_hash_never_reaches_local_save(self):
        s=MemoryStore();p=s.publish(b'first',self.frame());s.files[p['path']]=b'wrong'
        with patch.object(ledger_metadata,'read_upload') as parse:
            with self.assertRaises(store.StoreError):s.download()
            parse.assert_not_called()

    def test_public_repository_is_rejected_before_data_write(self):
        s=store.GitHubStore({'repository':'example/ledger','token':'secret','branch':'main'})
        with patch.object(s,'request',return_value={'private':False}) as request:
            with self.assertRaises(store.StoreError):s.publish(b'private',self.frame())
            self.assertEqual(request.call_count,1)

    def test_http_errors_do_not_expose_credentials_or_response(self):
        s=store.GitHubStore({'repository':'example/ledger','token':'sensitive-token','branch':'main'})
        s.opener=Mock();s.opener.open.side_effect=urllib.error.HTTPError('url',403,'sensitive-token',{},None)
        with self.assertRaises(store.StoreError) as error:s.request()
        self.assertNotIn('sensitive-token',str(error.exception))

    def test_transient_reads_retry_but_writes_are_not_repeated(self):
        s=store.GitHubStore({'repository':'example/ledger','token':'secret','branch':'main'})
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=b'{"private":true}'
        failure=lambda:urllib.error.HTTPError('url',503,'temporary',{},None)
        s.opener=Mock();s.opener.open.side_effect=[failure(),failure(),response]
        with patch.object(store.time,'sleep'):
            self.assertTrue(s.request()['private'])
        self.assertEqual(s.opener.open.call_count,3)
        s.opener.open.reset_mock();s.opener.open.side_effect=failure()
        with self.assertRaises(store.StoreError):s.request('/contents/latest.json',body={})
        self.assertEqual(s.opener.open.call_count,1)

    def test_pointer_traversal_and_malformed_size_rejected(self):
        s=MemoryStore();s.publish(b'first',self.frame());p=s.pointer()[0]
        for change in [{'path':'../../history.pkl'},{'bytes':store.MAX_BYTES+1},{'sha256':'no-hash'}]:
            s.files['latest.json']=json.dumps({**p,**change}).encode()
            with self.assertRaises(store.StoreError):s.pointer()

    def test_disallowed_redirect_does_not_forward_authorization(self):
        self.assertIsNone(store.NoRedirect().redirect_request(None,None,302,'',{},'https://other.invalid'))

    def test_interrupted_local_write_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'history.pkl';path.write_bytes(b'previous')
            def interrupted(partial):
                partial.write_bytes(b'partial')
                raise OSError('disk interrupted')
            with self.assertRaises(OSError):store.write_atomic(path,interrupted)
            self.assertEqual(path.read_bytes(),b'previous')
            self.assertEqual(list(Path(temp).iterdir()),[path])

    def test_stale_quality_after_interrupted_install_is_rebuilt_from_operational(self):
        from test_v2_15_4 import load_app_namespace
        from test_strategies_v2_15_11 import history
        app=load_app_namespace();raw=history();raw.loc[0,'예가/기초(0%)']=float('nan')
        metadata={'version':'new-version','as_of':'2026-11-09','training_rows':len(raw)-1,'data_start_date':'2026-01-01','data_end_date':'2026-04-01'}
        raw.attrs[ledger_metadata.ATTR]=metadata
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp);raw.to_pickle(path/'new-version.pkl')
            (path/'history_quality.json').write_text(json.dumps({'invalid_target_rows':999,ledger_metadata.ATTR:{'version':'old-version'}}))
            app['DATA_DIR']=temp;app['HISTORY_QUALITY_FILE']=str(path/'history_quality.json')
            quality=app['load_history_quality'](app['prepare_history_frame'](raw))
            self.assertEqual(quality['invalid_target_rows'],1)
            self.assertEqual(quality[ledger_metadata.ATTR]['version'],'new-version')


if __name__=='__main__':unittest.main()

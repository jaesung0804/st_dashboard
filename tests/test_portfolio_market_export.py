import copy
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import export_portfolio_market_state as export
from research_backend_client import Client


def sha(body):return hashlib.sha256(body).hexdigest()


class Store(Client):
    def __init__(self):
        self.blobs={};self.entries=[];self.methods=[];self.head_reads=0
        self.manifest={};self.raw={}
        prices=b'date,ticker,open,high,low,close,adjusted_close,volume\n2026-10-08,A,1,1,1,1,1,1\n2026-10-09,A,1,1,1,1,1,1\n2026-10-07,B,1,1,1,1,1,1\n'
        for rel in export.FILES:
            body=prices if 'ohlcv' in rel else b'ticker,name\nA,Alpha\n'
            self.raw[rel]=body
            info=dict(type='file',size=len(body),sha256=sha(body))
            if 'ohlcv' in rel:
                packed=gzip.compress(body);n=len(packed)//2
                parts=[f'.parts/{rel}/part-{i:04d}' for i in range(2)]
                for path,part in zip(parts,[packed[:n],packed[n:]]):self.add(path,part)
                info.update(type='split',encoding='gzip',parts=parts)
            else:self.add(rel,body)
            self.manifest[rel]=info
        # An unrelated model must neither be downloaded nor exported.
        self.add('data/dashboard_ews/private-model.bin',b'unrelated-model')
        self.manifest['data/dashboard_ews/private-model.bin']=dict(type='file',size=15,sha256=sha(b'unrelated-model'))
        self.add('state-manifest.json',json.dumps(self.manifest).encode())
    def add(self,path,body):
        self.blobs[sha(body)]=body
        self.entries.append(dict(relative_path=path,sha256=sha(body),byte_size=len(body)))
    def request(self,method,path,body=None,file=None):
        self.methods.append(method)
        assert method=='GET' and body is None and file is None
        if path=='/snapshot-heads/pipeline-state':
            self.head_reads+=1
            return io.BytesIO(json.dumps(dict(snapshot_id='a'*32 if self.head_reads==1 else 'b'*32)).encode())
        if path.startswith('/snapshots/'):
            return io.BytesIO(json.dumps(dict(items=self.entries,next_cursor=None)).encode())
        return io.BytesIO(self.blobs[path.removeprefix('/files/')])


class ExportTests(unittest.TestCase):
    def test_read_restore_export_and_new_head_keep_one_consistent_snapshot(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(export,'ROOT',Path(directory)):
            root=Path(directory)/'.research-backend/artifacts/example';store=Store()
            report=export.export(root,store)
            self.assertEqual(report['source_snapshot_id'],'a'*32)
            self.assertEqual(report['current_head_snapshot_id'],'b'*32)
            self.assertEqual(report['coverage']['us']['latest_date_counts'],{'2026-10-07':1,'2026-10-09':1})
            self.assertEqual(set(store.methods),{'GET'})
            with tarfile.open(root/'market-inputs.tar.gz') as archive:
                self.assertEqual(set(archive.getnames()),set(export.FILES)|{'export.json'})
                for rel,body in store.raw.items():self.assertEqual(archive.extractfile(rel).read(),body)
            self.assertEqual(export.digest(root/'market-inputs.tar.gz'),json.loads((root/'receipt.json').read_text())['bundle_sha256'])
            with self.assertRaises(FileExistsError):export.export(root,store)
    def test_corrupted_download_cannot_create_a_completed_export(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(export,'ROOT',Path(directory)):
            root=Path(directory)/'.research-backend/artifacts/example';store=Store()
            digest=store.entries[0]['sha256'];store.blobs[digest]=b'corruption'
            with self.assertRaises(ValueError):export.export(root,store)
            self.assertFalse((root/'receipt.json').exists())
    def test_manifest_limits_paths_missing_hash_and_missing_sources(self):
        store=Store();entries={e['relative_path']:e for e in store.entries}
        for mutation in ['missing','hash','size','traversal','duplicate','type']:
            with self.subTest(mutation=mutation):
                manifest=copy.deepcopy(store.manifest);info=manifest[export.FILES[0]]
                if mutation=='missing':del manifest[export.FILES[-1]]
                if mutation=='hash':info.pop('sha256')
                if mutation=='size':info['size']=export.MAX_RESTORED+1
                if mutation=='traversal':info['parts'][0]='../../secret'
                if mutation=='duplicate':info['parts'][1]=info['parts'][0]
                if mutation=='type':info['type']='archive'
                with self.assertRaises(ValueError):export.selected_sources(manifest,entries)
        with patch.object(export,'MAX_PACKED',1):
            with self.assertRaises(ValueError):export.selected_sources(store.manifest,entries)
    def test_real_client_guard_refuses_writes_before_network(self):
        client=object.__new__(export.ReadOnlyClient)
        for method in ['PUT','POST','DELETE','PATCH']:
            with self.assertRaises(ValueError):client.request(method,'/snapshot-heads/pipeline-state')


if __name__=='__main__':unittest.main()

import hashlib
import io
import tarfile
import pytest
from experiments.scripts.recover_tb_registry_asset import recover_member,RegistryRedirect
import urllib.request


def test_only_registered_input_is_recovered_and_its_bytes_are_verified(tmp_path):
    blob=tmp_path/'layer.tar.gz'
    data=b'original task input'
    with tarfile.open(blob,'w:gz') as archive:
        for name,body in [('app/answer.py',b'do not extract'),('app/isos/input.img',data),('../escape',b'bad')]:
            item=tarfile.TarInfo(name);item.size=len(body);archive.addfile(item,io.BytesIO(body))
    target=tmp_path/'input.img'
    recover_member(blob,'/app/isos/input.img',target,hashlib.sha256(data).hexdigest())
    assert target.read_bytes()==data
    assert sorted(p.name for p in tmp_path.iterdir())==['input.img','layer.tar.gz']
    with pytest.raises(RuntimeError,match='checksum mismatch'):
        recover_member(blob,'/app/isos/input.img',tmp_path/'wrong.img','0'*64)


def test_registry_authorization_is_not_forwarded_to_blob_cdn():
    request=urllib.request.Request('https://registry.example/v2/repo/blobs/hash',
        headers={'Authorization':'Bearer anonymous-registry-token'})
    handler=RegistryRedirect()
    redirected=handler.redirect_request(request,None,302,'Found',{},'https://cdn.example/blob')
    assert not redirected.has_header('Authorization')
    same=handler.redirect_request(request,None,302,'Found',{},'https://registry.example/blob')
    assert same.get_header('Authorization')=='Bearer anonymous-registry-token'


def test_parallel_ranges_reassemble_exact_original_bytes(tmp_path):
    import http.server
    import threading
    from experiments.scripts.recover_tb_exact_asset import segmented_download
    data=bytes(range(256))*100
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            start,end=map(int,self.headers['Range'].removeprefix('bytes=').split('-'))
            body=data[start:end+1]
            self.send_response(206)
            self.send_header('Content-Range',f'bytes {start}-{end}/{len(data)}')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    output=tmp_path/'attempt';output.mkdir();cache=tmp_path/'cache';cache.mkdir()
    try:
        segmented_download(f'http://127.0.0.1:{server.server_port}/input','input.bin',
            hashlib.sha256(data).hexdigest(),len(data),2,output,cache,segments=5)
        assert (cache/'input.bin').read_bytes()==data
    finally:server.shutdown();server.server_close();thread.join()

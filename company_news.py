"""Manual, fixed Yahoo TSLA headline feed download with original evidence archive."""
import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, HTTPRedirectHandler, build_opener
from urllib.parse import urlsplit
from sentiment.sentiment_sources import parse_feed

URL='https://feeds.finance.yahoo.com/rss/2.0/headline?s=TSLA&region=US&lang=en-US'


class OfficialFeedRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        parts=urlsplit(newurl)
        if parts.scheme!='https' or parts.hostname not in ('feeds.finance.yahoo.com','finance.yahoo.com') or parts.username or parts.password or parts.port not in (None,443):
            raise ValueError('订阅重定向超出指定Yahoo来源，已拒绝')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def download(desk):
    # Known public URL only, using system proxy settings without bypassing TLS.
    request=Request(URL,headers={'User-Agent':'HuaiceQuant/3.0 public research','Accept-Encoding':'identity'})
    with build_opener(OfficialFeedRedirect()).open(request,timeout=15) as response:
        raw=response.read(3*1024*1024+1)
    if len(raw)>3*1024*1024 or b'<!doctype' in raw.lower() or b'<!entity' in raw.lower():
        raise ValueError('订阅过大或不是安全RSS XML')
    rows=parse_feed(raw,source='media',default_asset='TSLA',max_text_chars=None,require_published=True)
    if not rows or len(rows)>2000:raise ValueError('没有可解析新闻或条目过多')
    for row in rows:
        row['资产']='TSLA';row['内容类型']='opinion'
    folder=desk.root/'downloads'/('TSLA_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    folder.mkdir(parents=True)
    (folder/'original_feed.xml').write_bytes(raw)
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    (folder/'tsla_news.csv').write_text(stream.getvalue(),encoding='utf-8-sig')
    sources=desk.snapshot()['sources'];existing=next((s for s in sources if s['url']==URL),None)
    desk.save_source(dict(name='Yahoo Finance · TSLA公司新闻',url=URL,category='media',content_type='opinion',asset='TSLA',**({'id':existing['id']} if existing else {})))
    task=desk.import_csv(stream.getvalue(),'Yahoo Finance · TSLA公司新闻（真实RSS下载）')
    result=dict(**task,url=URL,received=len(rows),raw_sha256=hashlib.sha256(raw).hexdigest(),archive=str(folder),note='实际下载的新闻摘要，不是公司官方公告。资产按订阅关联，事件识别还会检查正文；真实性待核验，不把新闻直接当交易指令。')
    (folder/'manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result

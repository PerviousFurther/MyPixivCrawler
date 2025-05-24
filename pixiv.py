import pathlib as pth

import urllib.error as urlerr
import urllib.request as rqs
import urllib.parse as prs
# import http.cookiejar as ckj
# import html.parser as psr
import json
import io

import datetime as dtm

from concurrent.futures import ThreadPoolExecutor
import threading as thread

import http
import http.client as client
import http.cookiejar as ckj

def _url_get(headers, url, timeout=5):
   return rqs.urlopen(rqs.Request(url, headers=headers, method='GET'), timeout=timeout)

def _illust_url_from_illust(uid):
    return f"https://www.pixiv.net/ajax/illust/{uid}"

def _illust_urls_from_user(headers, userid):
    url = f"https://www.pixiv.net/ajax/user/{userid}/profile/all"
    with _url_get(headers, url) as resp:           
       content = json.load(resp)["body"]
       return [_illust_url_from_illust(i) 
               for i in content['illusts']]

def _mkurl_from_tag_and_page(tag, page):
    return f"https://www.pixiv.net/ajax/search/artworks/{prs.quote(tag)}?p={page}"

def _illust_urls_from_tag(headers, tag):
    result = []
    i, page = 1, 1
    while i <= page:
        with _url_get(headers, _mkurl_from_tag_and_page(tag, i)) as resp:
            content = json.load(resp)["body"]["illustManga"]
        if i == 1:
            page = content["lastPage"]
            print(f"[MESSAGE] <{tag}:tag> has pages: {page}")
        [result.append(_illust_url_from_illust(data["id"])) for data in content["data"]]
        print(f"[MESSAGE] Found new illust num: {len(content["data"])} at page {i}.")
        i += 1
    print(f"[MESSAGE] Total illust num of <{tag}:tag> {len(content["data"])}.")
    return result

def _pull_and_save(headers, img_url, operate, *args):
    """Download from image url and save image to `pth`"""
    try:
        with _url_get(headers, img_url) as resp:
            return operate(resp.read(), *args)
    except Exception as e:
        raise ExceptionGroup(f"When download: '{img_url}'", [e])

class no_such_page_error(Exception):
    pass

def _consume_exception(error):
    if hasattr(error, 'exceptions'):
        print(error)
        for suberr in error.exceptions:
            _consume_exception(suberr)
    else:
        print(error)

def _is_stop_signal(error):
    if isinstance(error, no_such_page_error):
        return True
    else:
        print("[ERROR]")
        _consume_exception(error)
        return False

def _retry_is_end(e, last_err, same_err_time):
    if _is_stop_signal(e): return True, 0, 0
    if type(last_err) == type(e):
        same_err_time += 1
        if same_err_time > 5:
            print("[FATAL] Same error have been occur multiple time, Aborting...")
            return True, 0, 0
    return False, e, same_err_time

def _retry(retry, default, fun, *args):
    last_err = None
    same_err_time = 0
    if retry is None:
        while True:
            try: return fun(*args)
            except Exception as e: 
               end, last_err, same_err_time = _retry_is_end(e, last_err, same_err_time)
               if end: return default
    else:
        for _ in range(retry):
            try: return fun(*args)
            except Exception as e:
                end, last_err, same_err_time = _retry_is_end(e, last_err, same_err_time)
                if end: return default

def _wait_all(futures):
    """Consume futures, ignore exceptions."""
    result = []
    for fut in futures:
        try:
            ret = fut.result()
            if ret is not None: 
                result.append(ret)
        except Exception as e: 
            print(f"[EXCEPTION] Uncaught exception: {e}.")
    if len(result):
        return result

def _mkpth_for_illust(content, dir):
    author = content["userName"].replace('/', '_').replace('\\', '_')
    # title = content["title"].replace('/', '_').replace('\\', '_')
    author_dir = pth.Path(dir / author)
    if not author_dir.exists() or not author_dir.is_dir():
        author_dir.mkdir()
    return author_dir #, title

def _ori_url_from_illust_page(headers, uid):
    """Obtain pages illust source."""
    with _url_get(headers, f"https://www.pixiv.net/ajax/illust/{uid}/pages") as resp:
        content = json.load(resp)
        if content["error"] != False:
            raise no_such_page_error();
        content = content["body"]
        return [file["urls"]["original"]
                for file in content]

def _write_image(data, path, transform):
    import PIL.Image as plw
    if transform:
        transform(plw.open(io.BytesIO(data))).save(path)
    else:
        plw.open(io.BytesIO(data)).save(path)
    print(f"[MESSAGE] Downloaded: '{path}'")
    return path

def _save_illust_0(state, headers, content, dir, transform):
    """Download normal png, jpg."""
    uid, author = content["illustId"], content["userName"]
    urls = _retry( None
                 , None
                 , _ori_url_from_illust_page
                 , headers
                 , uid)
    if urls is None:
        print(f"[MESSAGE] Illust '{content["illustId"]}' do not have page url.")
        urls = [content["urls"]["original"]]
    urls = state.decrease(author, uid, urls)
    author_dir = _mkpth_for_illust(content, dir)
    with ThreadPoolExecutor(max_workers=4) as executor:
        result = _wait_all([executor.submit(_retry
                                           , None
                                           , ""
                                           , _pull_and_save
                                           , headers
                                           , img_url
                                           , _write_image
                                           , author_dir / f"{img_url.split('/')[-1]}"
                                           , transform)
                                           for img_url in urls])
    state.set(author, uid, result)

def _unzip_and_make_gif(data, dir, transform, filename, duration):
    import zipfile as zp
    tempdir = dir / "temp"
    while tempdir.exists():
        print("[MESSAGE] Other thread are accessing the file, waiting...")
        thread.Event().wait(0.1)
    tempdir.mkdir()
    with zp.ZipFile(io.BytesIO(data), 'r') as zip:
        names = zip.namelist()
        zip.extractall(dir.absolute())
    print(f"[MESSAGE] File in zip: {names}")

    import PIL.Image as plw
    images = []
    for name in names:
        path = dir / name
        with plw.open(dir / name) as img:
            if transform:
                images.append(transform(img.copy()))
            else:
                images.append(img.copy())
        path.unlink()
    
    gif_pth = dir / filename
    images[0].save( gif_pth
                  , 'GIF'
                  , save_all=True
                  , append_images=images[1:]
                  , duration=duration
                  , optimize=False
                  , loop=0
                  , disposal=3)
    
    tempdir.rmdir()
    print(f"[MESSAGE] Downloaded: {gif_pth}.")
    return gif_pth

def _save_illust_2(state, headers, content, dir, transform):
    """Download as gif"""
    author_dir = _mkpth_for_illust(content, dir)
    uid, author = content["illustId"], content["userName"]
    with _url_get(headers, f"https://www.pixiv.net/ajax/illust/{uid}/ugoira_meta") as resp:
        content = json.load(resp)["body"]
    zip_url = content["originalSrc"]
    pths = _retry( None
                 , ""
                 , _pull_and_save
                 , headers
                 , zip_url
                 , _unzip_and_make_gif
                 , author_dir
                 , transform
                 , f"{uid}.gif"
                 , content["frames"][0]["delay"])
    state.set(author, uid, [pths])

def _download_illust(state, headers, url, dir, filter, transform):
    """Select download strategy."""
    with _url_get(headers, url) as resp:
        content = json.load(resp)["body"]

        if state.full(content["userName"], content["illustId"]):
            print(f"[MESSAGE] All '{content["illustId"]}' illust were downloaded.")
            return
        
        if filter:
            should = filter(content)
        else:
            should = True
        if should:
            match content["illustType"]:
                case 0: _save_illust_0(state, headers, content, dir, transform)
                case 1: _save_illust_0(state, headers, content, dir, transform)
                case 2: _save_illust_2(state, headers, content, dir, transform)
                case _: 
                    print(f"[DEVELOPMENT IMPORTANT!]\n Skip unknown type '{content["illustType"]}' of illust: '{content["illustId"]}'")
                    print(f" If you see this, plz contact with library.")
            
class _download_state:
    def __init__(self, download_dir):
        self.ddir = download_dir
        self.lock = thread.Lock()
        self.worked = {}

        self.ppth = self.ddir / "profile.json"
        if self.ppth.exists():
            with open(self.ppth, 'r') as f:
                self.worked = json.load(f)
            print(f"[MESSAGE] Profile '{self.ppth}' load success.")
        else:
            print(f"[MESSAGE] Profile '{self.ppth}' not exists.")

    def set(self, author, uid, values):
        self.lock.acquire()
        values = [str(value.name) for value in values]
        if author in self.worked:
            if self.worked[author]:
                if uid in self.worked[author]:
                    src = 0
                    liss = self.worked[author][uid]
                    for dst in range(len(liss)):
                        if liss[dst] == "":
                            liss[dst] = values[src]
                            src += 1
                else:
                    self.worked[author][uid] = values
            else:
                self.worked[author] = {uid:values}
        else:
            self.worked[author] = {uid:values}
        self.lock.release()

    def contain(self, author, uid):
        return author in self.worked and uid in self.worked[author]
    
    def decrease(self, author, uid, urls):
        return [url for url in urls if not self.contain(author, uid) or url.split('/')[-1] not in self.worked[author][uid]]
    
    def full(self, author, uid):
        return self.contain(author, uid) and "" not in self.worked[author][uid]
    
    def dump(self):
        with open(self.ppth, 'w') as f:
            json.dump(self.worked, f, indent=2)
    
    def __del__(self):
        self.dump()

def _mkdir_safe(dir):
    if not dir.exists() or not dir.is_dir():
        dir.mkdir()
    return dir

def download( values:int|str|list[int]|list[str]
            , mode:str='illust'
            , /, *
            , root_dir=pth.Path()
            , filter=None
            , transform=None):
    """
    Paramter:
        
    """
    print(f"Begin downloading image by <{values}:{mode}>")
    
    if not hasattr(values, '__getitem__'):
        values = [values]

    if filter:
        if not hasattr(filter, '__call__'):
            print("Filter is not valid.")
            filter = None

    if transform:
        if not hasattr(transform, '__call__'):
            print("Tranform is not valid.")
            transform = None

    headers = {
        # 'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8',
        # recommand by pixiv ajax.
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/97.0.4692.71 Safari/537.36",
        # "return-to": ""
        "Referer": "https://www.pixiv.net/",
        "Cookie": "p_ab_id=1; p_ab_id_2=4; p_ab_d_id=1013074219; yuid_b=OXWJdHU; privacy_policy_notification=0; a_type=0; b_type=1; cc1=2025-04-19%2001%3A46%3A32; PHPSESSID=51674374_hUKlnIGRzLaifJYAZ8RpFUx4ezXrQ4ft; device_token=10e9d22608865b9f15e6be11645b4570; c_type=35; privacy_policy_agreement=7; __cf_bm=WLnYZRpS5awE69NiJUq5YkqNlxtF9FuEHDq7BkwY1m8-1745069907-1.0.1.1-ez.k_N19ywZJ6u_BbLJ2xZ4KDisrIM3KMAlS216oelz1YoZ3LIT7FVpc2PaZvVk0pVo1kNdwAnKfWEDApaiXQVxBHTbQukNMFvHN1mmH9VS3V.RBO6aHK2XSNCkw7ode; cf_clearance=ISMGXrzlPL_e7XkSZT5BkUzVS0oS8FLc9VezRTjwSJU-1745069907-1.2.1.1-YCSTzcEI101E.cTmHQVvCbsHXpAs8ie10EWCt5MfpLVGNqPebgg1SvSaOzslnIT3.du0IH4zmW09g4.6VftY51p4jPiitN8q_ksUZeH1yps4KE56r6bWwRLTgbt0JNTtcicQVLeJ2BftuJ0dtGNuvUCAWNVlLymHvTTUSgRcxUR3mIkCdMorG0JBRQtgmyKeN98ndFnu.DL3GSdp4VNpD9pdVGNfjEtN0A8Z3Y9SnApbp4okJonu13MHmjVkKUIhqE8esJBp_Rgk_2pZD5Bffn3Bl02ILLxbFQQ78NWDf09nPCsdTs6lq3yAE03ZuzlZcDox1diOVRZtTNzAnAnMgWuE8k_JdAGsDAsuhsZS_ZI"
    }

    root_dir = _mkdir_safe(pth.Path(root_dir))
    print(f"Working directory is '{root_dir.absolute()}'")

    download_dir = _mkdir_safe(root_dir / "download")
    print(f"Download directory is '{download_dir.absolute()}'")
    
    # cache_dir = _mkdir_safe(root_dir / "cache")
    # print(f"Cache directory is '{cache_dir.absolute()}'")

    match mode:
        case 'illust':
            illust_urls = [_illust_url_from_illust(value) for value in values]
        case 'user':
            illust_urls = [item for urls in 
                           [_illust_urls_from_user(headers, value) for value in values] for item in urls]
        case 'tag':
            illust_urls = [item for urls in [_illust_urls_from_tag(headers, value) for value in values] for item in urls]
        case _:
            raise Exception(f"Unknown mode: '{mode}'")
        
    state = _download_state(download_dir)
    with ThreadPoolExecutor(max_workers=4) as executor:
        _wait_all([executor.submit( _retry
                                  , None
                                  , None
                                  , _download_illust
                                  , state
                                  , headers
                                  , url
                                  , download_dir
                                  , filter
                                  , transform) for url in illust_urls])
    state.dump()
    print(f"Finish downloading image of <{values}:{mode}>\n---")

class filter:
    index = 100
    def __call__(self, content):
        if content["userName"] in [
            prs.quote("イジャ 欧北"), prs.quote("うまごん")
        ]:
            should = False
        else:
            should = self.index > 0
            self.index -= 1
        return should

if __name__ == "__main__":
    # download('Virtuosa', 'tag', filter=filter())
    download([94520296, 24142381], 'user')
    # download(118147030)
    # download(129514133)


# _time_format = "%a, %d %b %Y %H:%M:%S GMT"
# _tz_gmt = tzinfo=dtm.timezone(dtm.timedelta(), 'GMT')
# _profile_name = "profile.json"
# _php_sess_id_name = "PHPSESSID"
# _expires_time_id_name = "Expire-time"
# _cookie_id_name = "Cookie"
# _pixiv_url = "https://www.pixiv.net/"
# _pixiv_login_url = "https://accounts.pixiv.net/login"

# def _dump_phpss_only(header):
#     cookie_str = header["Set-Cookie"]
#     print(f"Respond header:\n{header}")
#     print(f"Set-Cookie:\n {cookie_str}")
#     print(f"Parameter type: {type(cookie_str)}")

#     paramters = cookie_str.split(';')

#     _, psi = paramters[0].split('=')
#     print(f"Get token (PHPSESSID): {psi}")

#     expires_time = paramters[1].split('=')[1]
#     expires_time = dtm.datetime.strptime( expires_time
#                                         , _time_format).replace(tzinfo=_tz_gmt)
#     print(f"When the token expires: {expires_time}")

#     return {_php_sess_id_name: psi, _expires_time_id_name: expires_time}

# def _dump_cookie(header):
#     result = str()
#     for key, msg in header.items():
#         match key:
#             case "Set-Cookie":
#                 for value in msg.split(';'):
#                     if '=' in value:
#                         id, _ = value.split('=')
#                         if id in [ 'PHPSESSID'
#                                  , 'p_ab_id', 'p_ab_id_2', 'p_ab_d_id'
#                                  , 'yuid_b', 'privacy_policy_notification'
#                                  , 'a_type', 'b_type', 'cc1', 'device_token', 'c_type'
#                                  , '__cf_bm']:
#                             result = f"{result}{value}; "
#                         else:
#                            pass # print(f"Discard: '{value}'")

#         import hashlib
#         device_info = "WindowsX64"
#         timestamp = dtm.time().strftime(_time_format)
#         raw_string = device_info + timestamp
#         result += f"device_token={hashlib.md5(raw_string.encode('utf-8')).hexdigest()}; "
#         result += "privacy_policy_agreement=7; "
#         result += "c_type=35; "
#         result += "a_type=0; b_type=1; "
#         result += "yuid_b=TAwVsWA"
        
#     return {_cookie_id_name: result}
    
# def _login(profile_pth, headers):
#     print("Try connect pixiv.net...")
#     rqs.install_opener(
#         rqs.build_opener( 
#               rqs.HTTPCookieProcessor(
#                   ckj.CookieJar())
#             , rqs.HTTPRedirectHandler()))
#     with rqs.urlopen( rqs.Request( _pixiv_login_url
#                                  , headers=headers)
#                     , data=prs.urlencode({
#                         "grant-type": "password",
#                         "user-name": "PYTHON_CANNOT_UGLY_MORE",
#                         "password": "PYTHON_IS_UGLY",
#                       }).encode('utf-8')
#                     , timeout=10
#                     ) as resp:
#         code = resp.getcode()
#         if code != 200:
#             raise Exception(f"Request pixiv login page failed, code: {code}")
#         try:
#             # mmp = _dump_phpss_only(resp.headers)
#             # headers["PHPSESSID"] = mmp[_php_sess_id_name]
#             mmp = _dump_cookie(resp.headers)
#             headers[_cookie_id_name] = mmp[_cookie_id_name]
#         except TypeError:
#             print("[FATAL] This code was complete at 2025/4/19. Pixiv might already changed the package when login.")
#             print("        If you comming with these error, plz publish an issue to lib's github repository.")
#             print("        Or if you have solution that make it better, feel free to share it with us.")
#             raise Exception("The repository is not suitable for current version's pixiv")
#         try:
#             with open(profile_pth, 'w') as f:
#                 json.dump(mmp, f, indent=2)
#         except:
#             f.close()
#             profile_pth.unlink()
#             raise

#     print("Connect successfully.")
#     return headers

# def _is_profile_is_valid(path):
#     if path.exists() and path.is_file():
#         print(f"Find profile at: {path}.")
#         with open(path, 'r') as f:
#             profile = json.load(f)
#             date = dtm.datetime.strptime(profile[_expires_time_id_name], _time_format).replace(tzinfo=_tz_gmt)
#             now = dtm.datetime.now(tz=_tz_gmt)
#         if date < now:
#             return profile[_php_sess_id_name]
#         else:
#             print("But expired...")
#     else:
#         print(f"Cannot find profile at: '{path.absolute()}'")
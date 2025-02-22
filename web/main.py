import _thread

from flask import Flask, request, json, render_template, make_response
import settings
import log
from functions import system_exec_command, mysql_query
from monitor.movie_trailer import movie_trailer_all
from monitor.resiliosync import resiliosync_all
from rmt.media import transfer_directory
from rmt.qbittorrent import login_qbittorrent, get_qbittorrent_tasks, set_torrent_status, transfer_qbittorrent_task
from scheduler.autoremove_torrents import run_autoremovetorrents
from scheduler.hot_trailer import run_hottrailers
from scheduler.icloudpd import run_icloudpd
from scheduler.pt_signin import run_ptsignin
from scheduler.qb_transfer import run_qbtransfer
from scheduler.rss_download import run_rssdownload, add_qbittorrent_torrent
from scheduler.smzdm_signin import run_smzdmsignin
from scheduler.unicom_signin import run_unicomsignin
from web.emby.discord import report_to_discord
from web.emby.emby_event import EmbyEvent
from message.send import sendmsg
from flask_httpauth import HTTPBasicAuth
from werkzeug.security import generate_password_hash, check_password_hash

from web.wechat.WXBizMsgCrypt3 import WXBizMsgCrypt
import xml.etree.cElementTree as ET

# 菜单对应关系
WECHAT_MENU = {"_0_0": "/qbt", "_0_1": "/qbr", "_0_2": "/rss", "_0_3": "/hotm", "_0_4": "/mrt",
               "_1_0": "/ipd", "_1_1": "/rst",
               "_2_0": "/pts", "_2_1": "/smzdms", "_2_2": "/unicoms"}


def create_app():
    app = Flask(__name__)
    app.config['JSON_AS_ASCII'] = False
    auth = HTTPBasicAuth()
    users = {
        "admin": generate_password_hash(settings.get("root.login_password"))
    }

    @auth.verify_password
    def verify_password(username, password):
        if username in users and \
                check_password_hash(users.get(username), password):
            return username

    # Emby消息通知
    @app.route('/emby', methods=['POST', 'GET'])
    def emby():
        if request.method == 'POST':
            request_json = json.loads(request.form.get('data', {}))
        else:
            server_name = request.args.get("server_name")
            user_name = request.args.get("user_name")
            device_name = request.args.get("device_name")
            ip = request.args.get("ip")
            flag = request.args.get("flag")
            request_json = {"Event": "user.login",
                            "User": {"user_name": user_name, "device_name": device_name, "device_ip": ip},
                            "Server": {"server_name": server_name},
                            "Status": flag
                            }
        # log.debug("输入报文：" + str(request_json))
        event = EmbyEvent(request_json)
        report_to_discord(event)
        return 'Success'

    # DDNS消息通知
    @app.route('/ddns', methods=['POST'])
    def ddns():
        request_json = json.loads(request.data, {})
        log.debug("【DDNS】输入报文：" + str(request_json))
        text = request_json['text']
        content = text['content']
        sendmsg("【DDNS】IP地址变化", content)
        return '0'

    # 主页面
    @app.route('/', methods=['POST', 'GET'])
    @auth.login_required
    def main():
        # 读取定时服务配置
        scheduler_cfg_list = []
        tim_rssdownload = settings.get("scheduler.rssdownload_interval")
        sta_rssdownload = settings.get("scheduler.rssdownload_flag")
        scheduler_cfg_list.append({'name': 'RSS订阅下载器', 'time': tim_rssdownload, 'state': sta_rssdownload})
        tim_autoremovetorrents = settings.get("scheduler.autoremovetorrents_interval")
        sta_autoremovetorrents = settings.get("scheduler.autoremovetorrents_flag")
        scheduler_cfg_list.append({'name': 'qBittorrent删种', 'time': tim_autoremovetorrents, 'state': sta_autoremovetorrents})
        tim_qbtransfer = settings.get("scheduler.qbtransfer_interval")
        sta_qbtransfer = settings.get("scheduler.qbtransfer_flag")
        scheduler_cfg_list.append({'name': 'qBittorrent文件转移	', 'time': tim_qbtransfer, 'state': sta_qbtransfer})
        sta_resiliosync = settings.get("monitor.resiliosync_flag")
        scheduler_cfg_list.append({'name': 'ResilioSync文件转移', 'time': '实时监控', 'state': sta_resiliosync})
        tim_icloudpd = settings.get("scheduler.icloudpd_cron")
        sta_icloudpd = settings.get("scheduler.icloudpd_flag")
        scheduler_cfg_list.append({'name': 'iCloud照片同步', 'time': tim_icloudpd, 'state': sta_icloudpd})
        tim_hottrailers = settings.get("scheduler.hottrailer_cron")
        sta_hottrailers = settings.get("scheduler.hottrailer_flag")
        scheduler_cfg_list.append({'name': '热门电影预告更新', 'time': tim_hottrailers, 'state': sta_hottrailers})
        tim_ptsignin = settings.get("scheduler.ptsignin_cron")
        sta_ptsignin = settings.get("scheduler.ptsignin_flag")
        scheduler_cfg_list.append({'name': 'PT网站签到', 'time': tim_ptsignin, 'state': sta_ptsignin})
        tim_smzdmsignin = settings.get("scheduler.smzdmsignin_cron")
        sta_smzdmsignin = settings.get("scheduler.smzdmsignin_flag")
        scheduler_cfg_list.append({'name': 'PT网站签到', 'time': tim_smzdmsignin, 'state': sta_smzdmsignin})
        tim_unicomsignin = settings.get("scheduler.unicomsignin_cron")
        sta_unicomsignin = settings.get("scheduler.unicomsignin_flag")
        scheduler_cfg_list.append({'name': '联通营业厅签到', 'time': tim_unicomsignin, 'state': sta_unicomsignin})
        sta_movietrailer = settings.get("monitor.movie_flag")
        scheduler_cfg_list.append({'name': '新增电影预告下载', 'time': '实时监控', 'state': sta_movietrailer})

        # 读取日志配置
        logtype = settings.get("root.logtype")

        # 读取RSS配置
        # 读取配置
        rss_cfg_list = []
        rss_jobs = eval(settings.get("rss.rss_job"))
        for rss_job in rss_jobs:
            # 读取子配置
            job_cfg = {'job': rss_job}
            rssurl = settings.get("rss." + rss_job + "_rssurl")
            job_cfg['url'] = rssurl
            movie_type = eval(settings.get("rss." + rss_job + "_movie_type"))
            job_cfg['movie_type'] = movie_type
            movie_res = eval(settings.get("rss." + rss_job + "_movie_re"))
            job_cfg['movie_re'] = movie_res
            tv_res = eval(settings.get("rss." + rss_job + "_tv_re"))
            job_cfg['tv_re'] = tv_res
            # 存入配置列表
            rss_cfg_list.append(job_cfg)

        return render_template("main.html",
                               page="rss",
                               log_type=logtype,
                               scheduler_cfg_list=scheduler_cfg_list,
                               rss_cfg_list=rss_cfg_list
                               )

    # 事件响应
    @app.route('/do', methods=['POST'])
    def do():
        cmd = request.form.get("cmd")
        data = json.loads(request.form.get("data"))
        if cmd:
            if cmd == "rmt":
                p_name = data["name"]
                p_year = data["year"]
                p_path = data["path"]
                p_type = data["type"]
                p_season = data["season"]
                if p_path and p_name:
                    v_path = p_path.split("|")[0]
                    v_hash = p_path.split("|")[1]
                    done_flag = transfer_directory(in_from="qBittorrent", in_name=p_name, in_title=p_name, in_path=v_path,
                                                   in_year=p_year, in_type=p_type, in_season=p_season)
                    if v_hash and done_flag:
                        set_torrent_status(v_hash)
                else:
                    run_qbtransfer()
                return {"rmt_stderr": "0", "rmt_stdout": "处理成功！", "rmt_paths": get_qbittorrent_tasks()}

            if cmd == "rmt_qry":
                # 读取qBittorrent列表
                return {"rmt_paths": get_qbittorrent_tasks()}

            if cmd == "msg":
                title = data["title"]
                text = data["text"]
                retcode, retmsg = "", ""
                if title or text:
                    retcode, retmsg = sendmsg(title, text)
                return {"msg_code": retcode, "msg_msg": retmsg}

            if cmd == "set_qry":
                # 读取配置文件
                cfg = open(settings.get_config_path(), mode="r", encoding="utf8")
                config_str = cfg.read()
                cfg.close()
                return {"config_str": config_str}

            if cmd == "set":
                editer_str = data["editer_str"]
                if editer_str:
                    cfg = open(settings.get_config_path(), mode="w", encoding="utf8")
                    cfg.write(editer_str)
                    cfg.flush()
                    cfg.close()
                return {"retcode": 0}

            if cmd == "log_qry":
                log_list = mysql_query("SELECT id,type,name,text,time FROM system_log ORDER BY time DESC LIMIT 100")
                return {"log_list": log_list}

            if cmd == "sch":
                sch_item = data["item"]
                if sch_item == "sch_btn_autoremovetorrents":
                    run_autoremovetorrents()
                if sch_item == "sch_btn_qbtransfer":
                    run_qbtransfer()
                if sch_item == "sch_btn_icloudpd":
                    run_icloudpd()
                if sch_item == "sch_btn_hottrailers":
                    run_hottrailers()
                if sch_item == "sch_btn_ptsignin":
                    run_ptsignin()
                if sch_item == "sch_btn_smzdmsignin":
                    run_smzdmsignin()
                if sch_item == "sch_btn_unicomsignin":
                    run_unicomsignin()
                if sch_item == "sch_btn_movietrailer":
                    movie_trailer_all()
                if sch_item == "sch_btn_resiliosync":
                    resiliosync_all()
                if sch_item == "sch_btn_rssdownload":
                    run_rssdownload()
                return {"retmsg": "执行完成！", "item": sch_item}

            if cmd == "rss":
                for key, value in data.items():
                    print(key, value)
                    settings.set_value('rss', key, value)
                return {"retcode": 0}

    # 响应企业微信消息
    @app.route('/wechat', methods=['GET', 'POST'])
    def wechat():
        sToken = settings.get("wechat.Token")
        sEncodingAESKey = settings.get("wechat.EncodingAESKey")
        sCorpID = settings.get("wechat.corpid")
        wxcpt = WXBizMsgCrypt(sToken, sEncodingAESKey, sCorpID)
        sVerifyMsgSig = request.args.get("msg_signature")
        sVerifyTimeStamp = request.args.get("timestamp")
        sVerifyNonce = request.args.get("nonce")

        if request.method == 'GET':
            sVerifyEchoStr = request.args.get("echostr")
            log.info("收到微信验证请求: echostr=" + sVerifyEchoStr)
            ret, sEchoStr = wxcpt.VerifyURL(sVerifyMsgSig, sVerifyTimeStamp, sVerifyNonce, sVerifyEchoStr)
            if ret != 0:
                log.error("微信请求验证失败 VerifyURL ret: " + str(ret))
            # 验证URL成功，将sEchoStr返回给企业号
            return sEchoStr
        else:
            sReqData = request.data
            log.info("收到微信消息：" + str(sReqData))
            ret, sMsg = wxcpt.DecryptMsg(sReqData, sVerifyMsgSig, sVerifyTimeStamp, sVerifyNonce)
            if ret != 0:
                log.error("解密微信消息失败 DecryptMsg ret：" + str(ret))
            xml_tree = ET.fromstring(sMsg)
            reponse_text = ""
            try:
                msg_type = xml_tree.find("MsgType").text
                if msg_type == "event":
                    event_key = xml_tree.find("EventKey").text
                    log.info("点击菜单：" + event_key)
                    content = WECHAT_MENU[event_key.split('#')[2]]
                else:
                    content = xml_tree.find("Content").text
                    log.info("消息内容：" + content)
                    reponse_text = content
            except Exception as err:
                log.error("发生错误：" + str(err))
                return make_response("", 200)
            # 处理消息内容
            if content == "/qbr":
                _thread.start_new_thread(run_autoremovetorrents, ())
            if content == "/qbt":
                _thread.start_new_thread(run_qbtransfer, ())
            if content == "/ipd":
                _thread.start_new_thread(run_icloudpd, ())
            if content == "/hotm":
                _thread.start_new_thread(run_hottrailers, ())
            if content == "/pts":
                _thread.start_new_thread(run_ptsignin, ())
            if content == "/smzdms":
                _thread.start_new_thread(run_smzdmsignin, ())
            if content == "/unicoms":
                _thread.start_new_thread(run_unicomsignin, ())
            if content == "/mrt":
                _thread.start_new_thread(movie_trailer_all, ())
            if content == "/rst":
                _thread.start_new_thread(resiliosync_all, ())
            if content == "/rss":
                _thread.start_new_thread(run_rssdownload, ())
            else:
                if content.startswith("http://") or content.startswith("https://"):
                    # 添加种子任务
                    save_path = settings.get("qbittorrent.save_path")
                    try:
                        ret = add_qbittorrent_torrent(content, save_path)
                        if ret and ret.find("Ok") != -1:
                            log.info("【WEB】添加qBittorrent任务：" + content)
                            sendmsg("添加qBittorrent下载任务成功！")
                    except Exception as e:
                        log.error("【WEB】添加qBittorrent任务出错：" + str(e))
            return make_response(reponse_text, 200)

    return app

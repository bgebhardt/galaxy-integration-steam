import asyncio
import gzip
import hashlib
import json
import logging
import socket as sock
import struct
import ipaddress
from itertools import count
from typing import Awaitable, Callable, Dict, Optional, Any, List, NamedTuple, Iterator

import base64

import vdf

from websockets.client import WebSocketClientProtocol
from pprint import pformat

from .consts import EMsg, EResult, EAccountType, EFriendRelationship, EPersonaState
from .messages import steammessages_base_pb2, steammessages_clientserver_login_pb2, steammessages_auth_pb2, \
    steammessages_player_pb2, steammessages_clientserver_friends_pb2, steammessages_clientserver_pb2, \
    steammessages_chat_pb2, steammessages_clientserver_2_pb2, steammessages_clientserver_userstats_pb2, \
    steammessages_clientserver_appinfo_pb2, resolved_service_messages_pb2, \
    enums_pb2

from .steam_types import SteamId, ProtoUserInfo

from pprint import pformat

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
LOG_SENSITIVE_DATA = False

GET_APP_RICH_PRESENCE = "Community.GetAppRichPresenceLocalization#1"
GET_LAST_PLAYED_TIMES = 'Player.ClientGetLastPlayedTimes#1'
CLOUD_CONFIG_DOWNLOAD = 'CloudConfigStore.Download#1'
REQUEST_FRIEND_PERSONA_STATES = "Chat.RequestFriendPersonaStates#1"
GET_RSA_KEY = "Authentication.GetPasswordRSAPublicKey#1"
LOGIN_CREDENTIALS = "Authentication.BeginAuthSessionViaCredentials#1"
UPDATE_TWO_FACTOR = "Authentication.UpdateAuthSessionWithSteamGuardCode#1"
CHECK_AUTHENTICATION_STATUS = "Authentication.PollAuthSessionStatus#1"


class SteamLicense(NamedTuple):
    license: steammessages_clientserver_pb2.CMsgClientLicenseList.License  # type: ignore[name-defined]
    shared: bool


class ProtobufClient:
    _PROTO_MASK = 0x80000000
    _ACCOUNT_ID_MASK = 0x0110000100000000
    _IP_OBFUSCATION_MASK = 0x606573A4
    _MSG_PROTOCOL_VERSION = 65580
    _MSG_CLIENT_PACKAGE_VERSION = 1561159470

    def __init__(self, set_socket : WebSocketClientProtocol):
        self._socket :                      WebSocketClientProtocol = set_socket
        #new auth flow
        self.rsa_handler:                   Optional[Callable[[EResult, int, int, int], Awaitable[None]]] = None
        self.login_handler:                 Optional[Callable[[EResult,steammessages_auth_pb2.CAuthentication_BeginAuthSessionViaCredentials_Response], Awaitable[None]]] = None
        self.two_factor_update_handler:     Optional[Callable[[EResult, str], Awaitable[None]]] = None
        self.poll_status_handler:           Optional[Callable[[EResult, steammessages_auth_pb2.CAuthentication_PollAuthSessionStatus_Response], Awaitable[None]]] = None
        self.revoke_refresh_token_handler:  Optional[Callable[[EResult], Awaitable[None]]] = None
        #old auth flow. Used to confirm login and repeat logins using the refresh token.
        self.log_on_token_handler:          Optional[Callable[[EResult], Awaitable[None]]] = None
        self._heartbeat_task:               Optional[asyncio.Task] = None #keeps our connection alive, essentially, by pinging the steam server. 
        self.log_off_handler:               Optional[Callable[[EResult], Awaitable[None]]] = None
        #retrive information
        self.relationship_handler:          Optional[Callable[[bool, Dict[int, EFriendRelationship]], Awaitable[None]]] = None
        self.user_info_handler:             Optional[Callable[[int, ProtoUserInfo], Awaitable[None]]] = None
        self.user_nicknames_handler:        Optional[Callable[[dict], Awaitable[None]]] = None
        self.license_import_handler:        Optional[Callable[[int], Awaitable[None]]] = None
        self.app_info_handler:              Optional[Callable] = None
        self.package_info_handler:          Optional[Callable[[], None]] = None
        self.translations_handler:          Optional[Callable[[int, Any], Awaitable[None]]] = None
        self.stats_handler:                 Optional[Callable[[int, Any, Any], Awaitable[None]]] = None
        self.user_authentication_handler:   Optional[Callable[[str, Any], Awaitable[None]]] = None
        self.confirmed_steam_id:            Optional[int] = None #this should only be set when the steam id is confirmed. this occurs when we actually complete the login. before then, it will cause errors. 
        self.times_handler:                 Optional[Callable[[int, int, int], Awaitable[None]]] = None
        self.times_import_finished_handler: Optional[Callable[[bool], Awaitable[None]]] = None
        self._session_id:                   Optional[int] = None
        self._job_id_iterator:              Iterator[int] = count(1) #this is actually clever. A lazy iterator that increments every time you call next. 
        #self.job_list : List[Dict[str,str]] = []

        self.account_info_retrieved = asyncio.Event()
        self.collections = {'event': asyncio.Event(),
                            'collections': dict()}

    async def close(self, send_log_off):
        if send_log_off:
            await self.send_log_off_message()
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()

    async def wait_closed(self):
        pass

    async def run(self):
        while True:
            #for job in self.job_list.copy():
            #    logger.info(f"New job on list {job}")
            #    if job['job_name'] == "import_game_stats":
            #        await self._import_game_stats(job['game_id'])
            #        self.job_list.remove(job)
            #    elif job['job_name'] == "import_collections":
            #        await self._import_collections()
            #        self.job_list.remove(job)
            #    elif job['job_name'] == "import_game_times":
            #        await self._import_game_time()
            #        self.job_list.remove(job)
            #    else:
            #        logger.warning(f'Unknown job {job}')
            try:
                packet = await asyncio.wait_for(self._socket.recv(), 10)
                logger.info("Received Packet" + str(packet))
                await self._process_packet(packet)
            except asyncio.TimeoutError:
                pass

    async def _send_service_method_with_name(self, message, target_job_name: str):
        emsg = EMsg.ServiceMethodCallFromClientNonAuthed if self.confirmed_steam_id is None else EMsg.ServiceMethodCallFromClient;
        job_id = next(self._job_id_iterator)
        await self._send(emsg, message, source_job_id=job_id, target_job_name= target_job_name)        

    #new workflow:  say hello -> get rsa public key -> log on with password -> handle steam guard -> confirm login
    #each is getting a dedicated function so i don't go insane.
    #confirm login is the old log_on_token call.

    async def say_hello(self):
        """Say Hello to the server. Necessary before sending non-authed calls.
            
            If we don't do this, they will just shut down the websocket connection gracefully with "OK", which is most definitely not "OK"
        """
        message = steammessages_clientserver_login_pb2.CMsgClientHello()
        message.protocol_version = self._MSG_PROTOCOL_VERSION
        await self._send(EMsg.ClientHello,message)

    #send the get rsa key request
    #imho we should just do a send and receive back to back instead of this bouncing around, but whatever. 
    async def get_rsa_public_key(self, account_name: str):
        message = steammessages_auth_pb2.CAuthentication_GetPasswordRSAPublicKey_Request()
        message.account_name = account_name
        await self._send_service_method_with_name(message, GET_RSA_KEY) #parsed from SteamKit's gobbledygook   

    #process the received the rsa key response. Because we will need all the information about this process, we send the entire message up the chain.
    async def _process_rsa(self, result, body):
        message = steammessages_auth_pb2.CAuthentication_GetPasswordRSAPublicKey_Response()
        message.ParseFromString(body)
        logger.info("Received RSA KEY")
        #logger.info(pformat(message))
        if (self.rsa_handler is not None):
            await self.rsa_handler(result, int(message.publickey_mod, 16), int(message.publickey_exp, 16), message.timestamp)
        else:
            logger.warning("NO RSA HANDLER SET!")

    async def log_on_password(self, account_name, enciphered_password: str, timestamp: int, os_value):
        friendly_name: str = sock.gethostname() + " (GOG Galaxy)"
        
        #device details is readonly. So we can't do this the easy way. 
        #device_details = steammessages_auth_pb2.CAuthentication_DeviceDetails()
        #device_details.device_friendly_name = 
        #device_details.os_type = os_value if os_value >= 0 else 0
        #device_details.platform_type= steammessages_auth_pb2.EAuthTokenPlatformType.k_EAuthTokenPlatformType_SteamClient

        message = steammessages_auth_pb2.CAuthentication_BeginAuthSessionViaCredentials_Request()

        message.account_name = account_name
        message.encrypted_password = base64.b64encode(enciphered_password) #i think it needs to be in this format, we can try doing it without after if it doesn't work. 
        message.website_id = "Client"
        message.device_friendly_name = friendly_name
        message.encryption_timestamp = timestamp
        message.platform_type = steammessages_auth_pb2.EAuthTokenPlatformType.k_EAuthTokenPlatformType_SteamClient #no idea if this line will work.
        #message.persistence = enums_pb2.ESessionPersistence.k_ESessionPersistence_Persistent # this is the default value and i have no idea how reflected enums work in python.
        
        #message.device_details = device_details
        #so let's do it the hard way. 
        message.device_details.device_friendly_name = friendly_name
        message.device_details.os_type = os_value if os_value >= 0 else 0
        message.device_details.platform_type= steammessages_auth_pb2.EAuthTokenPlatformType.k_EAuthTokenPlatformType_SteamClient
        #message.guard_data = ""
        logger.info("Sending log on message using credentials in new authorization workflow")

        await self._send_service_method_with_name(message, LOGIN_CREDENTIALS)

    async def _process_login(self, result, body):
        message = steammessages_auth_pb2.CAuthentication_BeginAuthSessionViaCredentials_Response()
        message.ParseFromString(body)
        logger.info("Processing Login Response!")
        """
        client_id : int #the id assigned to us.
        steamid : int #the id of the user that signed in
        request_id : bytes #unique request id. needed for the polling function.
        interval : float #interval to poll on.
        allowed_confirmations : List[CAuthentication_AllowedConfirmation] #possible ways to authenticate for 2FA if needed. 
        #    Note: Possible values:
        #       k_EAuthSessionGuardType_Unknown, k_EAuthSessionGuardType_None, k_EAuthSessionGuardType_EmailCode = 2, k_EAuthSessionGuardType_DeviceCode = 3,
        #       k_EAuthSessionGuardType_DeviceConfirmation = 4, k_EAuthSessionGuardType_EmailConfirmation = 5, k_EAuthSessionGuardType_MachineToken = 6,
        #   For the sake of copypasta, we're only supporting EmailCode, DeviceCode, and None. Unknown is expected, somewhat, but it's an error. 
        weak_token : string #ignored
        agreement_session_url: string #ignored?
        extended_error_message : string #used for errors. 
        """
        ##TODO: IF WE GET ERRORS UNSET THIS. 
        #if (self.steam_id is None and message.steamid is not None):
        #    self.steam_id = message.steamidd
        if (self.login_handler is not None):
            await self.login_handler(result, message)
        else:
            logger.warning("NO LOGIN HANDLER SET!")

    async def update_steamguard_data(self, client_id: int, steam_id:int, code:str, code_type:steammessages_auth_pb2.EAuthSessionGuardType):
        message = steammessages_auth_pb2.CAuthentication_UpdateAuthSessionWithSteamGuardCode_Request()

        message.client_id = client_id
        message.steamid= steam_id
        message.code = code
        message.code_type = code_type

        await self._send_service_method_with_name(message, UPDATE_TWO_FACTOR)

    async def _process_steamguard_update(self, result, body):
        message = steammessages_auth_pb2.CAuthentication_UpdateAuthSessionWithSteamGuardCode_Response()
        message.ParseFromString(body)
        logger.info("Processing Two Factor Response!")
        #this gives us a confirm url, but as of this writing we can ignore it. so, just the result is necessary. 
        
        if (self.two_factor_update_handler is not None):
            await self.two_factor_update_handler(result, message.agreement_session_url)
        else:
            logger.warning("NO TWO-FACTOR HANDLER SET!")

    async def poll_auth_status(self, client_id:int, request_id:bytes):
        message = steammessages_auth_pb2.CAuthentication_PollAuthSessionStatus_Request()
        message.client_id = client_id
        message.request_id = request_id

        await self._send_service_method_with_name(message, CHECK_AUTHENTICATION_STATUS)

    async def _process_auth_poll_status(self, result, body):
        message = steammessages_auth_pb2.CAuthentication_PollAuthSessionStatus_Response()
        message.ParseFromString(body)

        if (self.poll_status_handler is not None):
            await self.poll_status_handler(result, message)
        else:
            logger.warning("NO POLL STATUS HANDLER SET!")

    #old auth flow. Still necessary for remaining logged in and confirming after doing the new auth flow. 
    async def _get_obfuscated_private_ip(self) -> int:
        logger.info('Websocket state is: %s' % self._socket.state.name)
        await self._socket.ensure_open()
        host, port = self._socket.local_address
        ip = int(ipaddress.IPv4Address(host))
        obfuscated_ip = ip ^ self._IP_OBFUSCATION_MASK
        logger.debug(f"Local obfuscated IP: {obfuscated_ip}")
        return obfuscated_ip

    async def send_log_on_token_message(self, account_name: str, access_token: str, cell_id: int, machine_id: bytes, os_value: int):
        message = steammessages_clientserver_login_pb2.CMsgClientLogon()
        message.protocol_version = self._MSG_PROTOCOL_VERSION
        message.client_package_version = self._MSG_CLIENT_PACKAGE_VERSION
        message.cell_id = cell_id
        message.client_language = "english"
        message.client_os_type = os_value if os_value >= 0 else 0
        message.obfuscated_private_ip.v4 = await self._get_obfuscated_private_ip()
        message.qos_level = 3
        message.machine_id = machine_id
        message.account_name = account_name
        message.eresult_sentryfile = EResult.FileNotFound
        message.machine_name = sock.gethostname()
        message.access_token = access_token
        logger.info("Sending log on message using access token")
        await self._send(EMsg.ClientLogon, message)

    async def _heartbeat(self, interval):
        message = steammessages_clientserver_login_pb2.CMsgClientHeartBeat()
        while True:
            await asyncio.sleep(interval)
            await self._send(EMsg.ClientHeartBeat, message)

    async def _process_client_log_on_response(self, body):
        logger.debug("Processing message ClientLogOnResponse")
        message = steammessages_clientserver_login_pb2.CMsgClientLogonResponse()
        message.ParseFromString(body)
        result = message.eresult
        interval = message.heartbeat_seconds
        if result == EResult.OK:
            self.confirmed_steam_id = message.client_supplied_steamid
            self._heartbeat_task = asyncio.create_task(self._heartbeat(interval))
        else:
            logger.info(f"Failed to log on, reason : {message}")

        if self.log_on_token_handler is not None:
            await self.log_on_token_handler(result, )

    async def send_log_off_message(self):
        message = steammessages_clientserver_login_pb2.CMsgClientLogOff()
        logger.info("Sending log off message")
        try:
            await self._send(EMsg.ClientLogOff, message)
        except Exception as e:
            logger.error(f"Unable to send logoff message {repr(e)}")

    async def _import_game_stats(self, game_id):
        logger.info(f"Importing game stats for {game_id}")
        message = steammessages_clientserver_userstats_pb2.CMsgClientGetUserStats()
        message.game_id = int(game_id)
        await self._send(EMsg.ClientGetUserStats, message)

    async def _import_game_time(self):
        logger.info("Importing game times")
        job_id = next(self._job_id_iterator)
        message = steammessages_player_pb2.CPlayer_GetLastPlayedTimes_Request()
        message.min_last_played = 0
        await self._send(EMsg.ServiceMethodCallFromClient, message, job_id, None, GET_LAST_PLAYED_TIMES)

    async def set_persona_state(self, state):
        message = steammessages_clientserver_friends_pb2.CMsgClientChangeStatus()
        message.persona_state = state
        await self._send(EMsg.ClientChangeStatus, message)

    async def get_friends_statuses(self):
        job_id = next(self._job_id_iterator)
        message = steammessages_chat_pb2.CChat_RequestFriendPersonaStates_Request()
        await self._send(EMsg.ServiceMethodCallFromClient, message, job_id, None, REQUEST_FRIEND_PERSONA_STATES)

    async def get_user_infos(self, users, flags):
        message = steammessages_clientserver_friends_pb2.CMsgClientRequestFriendData()
        message.friends.extend(users)
        message.persona_state_requested = flags
        await self._send(EMsg.ClientRequestFriendData, message)

    async def _import_collections(self):
        job_id = next(self._job_id_iterator)
        message = resolved_service_messages_pb2.CCloudConfigStore_Download_Request()
        message_inside = resolved_service_messages_pb2.CCloudConfigStore_NamespaceVersion()
        message_inside.enamespace = 1
        message.versions.append(message_inside)
        await self._send(EMsg.ServiceMethodCallFromClient, message, job_id, None, CLOUD_CONFIG_DOWNLOAD)

    async def get_packages_info(self, steam_licenses: List[SteamLicense]):
        logger.info("Sending call %s with %d package_ids", repr(EMsg.ClientPICSProductInfoRequest), len(steam_licenses))
        message = steammessages_clientserver_appinfo_pb2.CMsgClientPICSProductInfoRequest()

        for steam_license in steam_licenses:
            info = message.packages.add()
            info.packageid = steam_license.license.package_id
            info.access_token = steam_license.license.access_token

        await self._send(EMsg.ClientPICSProductInfoRequest, message)

    async def get_apps_info(self, app_ids):
        logger.info("Sending call %s with %d app_ids", repr(EMsg.ClientPICSProductInfoRequest), len(app_ids))
        message = steammessages_clientserver_appinfo_pb2.CMsgClientPICSProductInfoRequest()

        for app_id in app_ids:
            info = message.apps.add()
            info.appid = app_id

        await self._send(EMsg.ClientPICSProductInfoRequest, message)

    async def get_presence_localization(self, appid, language='english'):
        logger.info(f"Sending call for rich presence localization with {appid}, {language}")
        message = resolved_service_messages_pb2.CCommunity_GetAppRichPresenceLocalization_Request()

        message.appid = appid
        message.language = language

        job_id = next(self._job_id_iterator)
        await self._send(EMsg.ServiceMethodCallFromClient, message, job_id, None,
                         target_job_name= GET_APP_RICH_PRESENCE)

    async def accept_update_machine_auth(self, jobid_target, sha_hash, offset, filename, cubtowrite):
        message = steammessages_clientserver_2_pb2.CMsgClientUpdateMachineAuthResponse()
        message.filename = filename
        message.eresult = EResult.OK
        message.sha_file = sha_hash
        message.getlasterror = 0
        message.offset = offset
        message.cubwrote = cubtowrite

        await self._send(EMsg.ClientUpdateMachineAuthResponse, message, None, jobid_target, None)

    async def _send(
        self,
        emsg,
        message,
        source_job_id=None,
        target_job_id=None,
        target_job_name=None
    ):
        proto_header = steammessages_base_pb2.CMsgProtoBufHeader()
        if self.confirmed_steam_id is not None:
            proto_header.steamid = self.confirmed_steam_id
        else:
            proto_header.steamid = 0 + self._ACCOUNT_ID_MASK
        if self._session_id is not None:
            proto_header.client_sessionid = self._session_id
        if source_job_id is not None:
            proto_header.jobid_source = source_job_id
        if target_job_id is not None:
            proto_header.jobid_target = target_job_id
        if target_job_name is not None:
            proto_header.target_job_name = target_job_name

        header = proto_header.SerializeToString()

        body = message.SerializeToString()
        data = struct.pack("<2I", emsg | self._PROTO_MASK, len(header))
        data = data + header + body

        if LOG_SENSITIVE_DATA:
            logger.info("[Out] %s (%dB), params:\n", repr(emsg), len(data), repr(message))
        else:
            logger.info("[Out] %s (%dB)", repr(emsg), len(data))
        await self._socket.send(data)

    async def _process_packet(self, packet):
        package_size = len(packet)
        logger.debug("Processing packet of %d bytes", package_size)
        if package_size < 8:
            logger.warning("Package too small, ignoring...")
        raw_emsg = struct.unpack("<I", packet[:4])[0]
        emsg: int = raw_emsg & ~self._PROTO_MASK
        if raw_emsg & self._PROTO_MASK != 0:
            header_len = struct.unpack("<I", packet[4:8])[0]
            header = steammessages_base_pb2.CMsgProtoBufHeader()
            header.ParseFromString(packet[8:8 + header_len])
            if header.client_sessionid != 0:
                if self._session_id is None:
                    logger.info("New session id: %d", header.client_sessionid)
                    self._session_id = header.client_sessionid
                if self._session_id != header.client_sessionid:
                    logger.warning('Received session_id %s while client one is %s', header.client_sessionId, self._session_id)
            await self._process_message(emsg, header, packet[8 + header_len:])
        else:
            logger.warning("Packet for %d -> EMsg.%s with extended header - ignoring", emsg, EMsg(emsg).name)

    async def _process_message(self, emsg: int, header, body):
        logger.info("[In] %d -> EMsg.%s", emsg, EMsg(emsg).name)
        if emsg == EMsg.Multi:
            await self._process_multi(body)
        elif emsg == EMsg.ClientLogOnResponse:
            await self._process_client_log_on_response(body)
        elif emsg == EMsg.ClientLoggedOff:
            await self._process_client_logged_off(body)
        elif emsg == EMsg.ClientFriendsList:
            await self._process_client_friend_list(body)
        elif emsg == EMsg.ClientGetAppOwnershipTicketResponse:
            await self._process_client_get_app_ownership_ticket_response(body)
        elif emsg == EMsg.ClientPersonaState:
            await self._process_client_persona_state(body)
        elif emsg == EMsg.ClientLicenseList:
            await self._process_license_list(body)
        elif emsg == EMsg.ClientPICSProductInfoResponse:
            await self._process_product_info_response(body)
        elif emsg == EMsg.ClientGetUserStatsResponse:
            await self._process_user_stats_response(body)
        elif emsg == EMsg.ClientAccountInfo:
            await self._process_account_info(body)
        elif emsg == EMsg.ClientNewLoginKey:
            await self._process_client_new_login_key(body, header.jobid_source)
        elif emsg == EMsg.ClientUpdateMachineAuth:
            await self._process_client_update_machine_auth(body, header.jobid_source)
        elif emsg == EMsg.ClientPlayerNicknameList:
            await self._process_user_nicknames(body)
        elif emsg == EMsg.ServiceMethod:
            await self._process_service_method_response(header.target_job_name, header.jobid_target, header.eresult, body)
        elif emsg == EMsg.ServiceMethodResponse:
            await self._process_service_method_response(header.target_job_name, header.jobid_target, header.eresult, body)
        else:
            logger.warning("Ignored message %d", emsg)

    async def _process_multi(self, body):
        logger.debug("Processing message Multi")
        message = steammessages_base_pb2.CMsgMulti()
        message.ParseFromString(body)
        if message.size_unzipped > 0:
            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(None, gzip.decompress, message.message_body)
        else:
            data = message.message_body

        data_size = len(data)
        offset = 0
        size_bytes = 4
        while offset + size_bytes <= data_size:
            size = struct.unpack("<I", data[offset:offset + size_bytes])[0]
            await self._process_packet(data[offset + size_bytes:offset + size_bytes + size])
            offset += size_bytes + size
        logger.debug("Finished processing message Multi")



    async def _process_client_update_machine_auth(self, body, jobid_source):
        logger.debug("Processing message ClientUpdateMachineAuth")
        message = steammessages_clientserver_2_pb2.CMsgClientUpdateMachineAuth()
        message.ParseFromString(body)

        sentry_sha = hashlib.sha1(message.bytes).digest()
        await self.user_authentication_handler('sentry', sentry_sha)
        await self.accept_update_machine_auth(jobid_source, sentry_sha, message.offset, message.filename, message.cubtowrite)

    async def _process_account_info(self, body):
        logger.debug("Processing message ClientAccountInfo")
        message = steammessages_clientserver_login_pb2.CMsgClientAccountInfo()
        message.ParseFromString(body)
        await self.user_authentication_handler('persona_name', message.persona_name)
        self.account_info_retrieved.set()

    async def _process_client_logged_off(self, body):
        logger.debug("Processing message ClientLoggedOff")
        message = steammessages_clientserver_login_pb2.CMsgClientLoggedOff()
        message.ParseFromString(body)
        result = message.eresult

        assert self._heartbeat_task is not None
        self._heartbeat_task.cancel()

        if self.log_off_handler is not None:
            await self.log_off_handler(result)

    async def _process_user_nicknames(self, body):
        logger.debug("Processing message ClientPlayerNicknameList")
        message = steammessages_clientserver_friends_pb2.CMsgClientPlayerNicknameList()
        message.ParseFromString(body)
        nicknames = {}
        for player_nickname in message.nicknames:
            nicknames[str(player_nickname.steamid)] = player_nickname.nickname

        await self.user_nicknames_handler(nicknames)

    async def _process_client_friend_list(self, body):
        logger.debug("Processing message ClientFriendsList")
        if self.relationship_handler is None:
            return

        message = steammessages_clientserver_friends_pb2.CMsgClientFriendsList()
        message.ParseFromString(body)
        friends = {}
        for relationship in message.friends:
            steam_id = relationship.ulfriendid
            details = SteamId.parse(steam_id)
            if details.type_ == EAccountType.Individual:
                friends[steam_id] = EFriendRelationship(relationship.efriendrelationship)

        await self.relationship_handler(message.bincremental, friends)

    async def _process_client_persona_state(self, body):
        logger.debug("Processing message ClientPersonaState")
        if self.user_info_handler is None:
            return

        message = steammessages_clientserver_friends_pb2.CMsgClientPersonaState()
        message.ParseFromString(body)

        for user in message.friends:
            user_id = user.friendid
            if user_id == self.confirmed_steam_id and int(user.game_played_app_id) != 0:
                await self.get_apps_info([int(user.game_played_app_id)])
            user_info = ProtoUserInfo()
            if user.HasField("player_name"):
                user_info.name = user.player_name
            if user.HasField("avatar_hash"):
                user_info.avatar_hash = user.avatar_hash
            if user.HasField("persona_state"):
                user_info.state = EPersonaState(user.persona_state)
            if user.HasField("gameid"):
                user_info.game_id = user.gameid
                rich_presence: Dict[str, str] = {}
                for element in user.rich_presence:
                    if type(element.value) == bytes:
                        logger.warning(f"Unsupported presence type: {type(element.value)} {element.value}")
                        rich_presence = {}
                        break
                    rich_presence[element.key] = element.value
                    if element.key == 'status' and element.value:
                        if "#" in element.value:
                            await self.translations_handler(user.gameid)
                    if element.key == 'steam_display' and element.value:
                        if "#" in element.value:
                            await self.translations_handler(user.gameid)
                user_info.rich_presence = rich_presence
            if user.HasField("game_name"):
                user_info.game_name = user.game_name

            await self.user_info_handler(user_id, user_info)

    async def _process_license_list(self, body):
        logger.debug("Processing message ClientLicenseList")
        if self.license_import_handler is None:
            return

        message = steammessages_clientserver_pb2.CMsgClientLicenseList()
        message.ParseFromString(body)

        licenses_to_check = []

        for license in message.licenses:
            # license.type 1024 = free games
            # license.flags 520 = unidentified trash entries (games which are not owned nor are free)
            if int(license.flags) == 520:
                continue

            if license.package_id == 0:
                # Packageid 0 contains trash entries for every user
                logger.debug("Skipping packageid 0 ")
                continue

            if int(license.owner_id) == int(self.confirmed_steam_id - self._ACCOUNT_ID_MASK):
                licenses_to_check.append(SteamLicense(license=license, shared=False))
            else:
                if license.package_id in licenses_to_check:
                    continue
                licenses_to_check.append(SteamLicense(license=license, shared=True))

        await self.license_import_handler(licenses_to_check)

    async def _process_product_info_response(self, body):
        logger.debug("Processing message ClientPICSProductInfoResponse")
        message = steammessages_clientserver_appinfo_pb2.CMsgClientPICSProductInfoResponse()
        message.ParseFromString(body)
        apps_to_parse = []

        def product_info_handler(packages, apps):
            for info in packages:
                self.package_info_handler()

                package_id = str(info.packageid)
                package_content = vdf.binary_loads(info.buffer[4:])
                package = package_content.get(package_id)
                if package is None:
                    continue

                for app in package['appids'].values():
                    appid = str(app)
                    self.app_info_handler(package_id=package_id, appid=appid)
                    apps_to_parse.append(app)

            for info in apps:
                app_content = vdf.loads(info.buffer[:-1].decode('utf-8', 'replace'))
                appid = str(app_content['appinfo']['appid'])
                try:
                    type_ = app_content['appinfo']['common']['type'].lower()
                    title = app_content['appinfo']['common']['name']
                    parent = None
                    if 'extended' in app_content['appinfo'] and type_ == 'dlc':
                        parent = app_content['appinfo']['extended']['dlcforappid']
                        logger.debug(f"Retrieved dlc {title} for {parent}")
                    if type == 'game':
                        logger.debug(f"Retrieved game {title}")
                    self.app_info_handler(appid=appid, title=title, type=type_, parent=parent)
                except KeyError:
                    logger.warning(f"Unrecognized app structure {app_content}")
                    self.app_info_handler(appid=appid, title='unknown', type='unknown', parent=None)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, product_info_handler, message.packages, message.apps)

        if len(apps_to_parse) > 0:
            logger.debug("Apps to parse: %s", str(apps_to_parse))
            await self.get_apps_info(apps_to_parse)

    async def _process_rich_presence_translations(self, body):
        message = resolved_service_messages_pb2.CCommunity_GetAppRichPresenceLocalization_Response()
        message.ParseFromString(body)

        # keeping info log for further rich presence improvements
        logger.info(f"Received information about rich presence translations for {message.appid}")
        await self.translations_handler(message.appid, message.token_lists)

    async def _process_user_stats_response(self, body):
        logger.debug("Processing message ClientGetUserStatsResponse")
        message = steammessages_clientserver_userstats_pb2.CMsgClientGetUserStatsResponse()
        message.ParseFromString(body)

        game_id = str(message.game_id)
        stats = message.stats
        achievement_blocks = message.achievement_blocks
        achievements_schema = vdf.binary_loads(message.schema, merge_duplicate_keys=False)

        self.stats_handler(game_id, stats, achievement_blocks, achievements_schema)

    async def _process_user_time_response(self, body):
        message = steammessages_player_pb2.CPlayer_GetLastPlayedTimes_Response()
        message.ParseFromString(body)
        for game in message.games:
            logger.debug(f"Processing game times for game {game.appid}, playtime: {game.playtime_forever} last time played: {game.last_playtime}")
            await self.times_handler(game.appid, game.playtime_forever, game.last_playtime)
        await self.times_import_finished_handler(True)

    async def _process_collections_response(self, body):
        message = resolved_service_messages_pb2.CCloudConfigStore_Download_Response()
        message.ParseFromString(body)

        for data in message.data:
            for entry in data.entries:
                try:
                    loaded_val = json.loads(entry.value)
                    self.collections['collections'][loaded_val['name']] = loaded_val['added']
                except:
                    pass
        self.collections['event'].set()

    async def _process_service_method_response(self, target_job_name, target_job_id, eresult, body):
        logger.info("Processing message ServiceMethodResponse %s", target_job_name)
        if target_job_name == GET_APP_RICH_PRESENCE:
            await self._process_rich_presence_translations(body)
        elif target_job_name == GET_LAST_PLAYED_TIMES:
            await self._process_user_time_response(body)
        elif target_job_name == CLOUD_CONFIG_DOWNLOAD:
            await self._process_collections_response(body)
        #elif target_job_name == REQUEST_FRIEND_PERSONA_STATES:
            #pass #no idea what to do here. for now, having it error will let me know when it's called so i can see wtf to do with it.
        elif target_job_name == GET_RSA_KEY:
            await self._process_rsa(eresult, body)
        elif target_job_name == LOGIN_CREDENTIALS:
            await self._process_login(eresult, body)
        elif target_job_name == UPDATE_TWO_FACTOR:
            await self._process_steamguard_update(eresult, body)
        elif target_job_name == CHECK_AUTHENTICATION_STATUS:
            await self._process_auth_poll_status(eresult, body)
        else:
            logger.warning("Unparsed message, no idea what it is. Tell me")
            logger.warning("job name: \"" + target_job_name + "\"")

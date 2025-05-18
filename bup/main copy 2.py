# functions/main.py
import os
import uuid
import random
import string
from datetime import datetime, timezone, timedelta
import traceback
import json

# Firebase / Google Cloud ライブラリ
from firebase_functions import https_fn, options # corsはoptions経由でアクセス
import firebase_admin
from firebase_admin import initialize_app, firestore, auth

# 型ヒント用
from google.cloud.firestore_v1.client import Client as FirestoreClient
from google.cloud.firestore_v1.document import DocumentReference
from google.cloud.firestore_v1.transaction import Transaction
from google.cloud.firestore_v1.base_query import FieldFilter
from google.api_core import exceptions as google_exceptions

# functions/main.py (Admin SDK遅延初期化テスト)
import json # Responseで使うので

# --- 全体的なオプション設定 ---
options.set_global_options(region=options.SupportedRegion.ASIA_NORTHEAST1)


# === 定数 ===
DEFAULT_USAGE_LIMIT = 100
PROCESSED_TRANSACTION_TTL_DAYS = 1
API_KEY_PREFIX = "sk_"
API_KEY_RANDOM_PART_LENGTH = 32

# === CORS設定値の定義 (generate_or_fetch_api_key 用) ===
# 環境変数から読み込む。デフォルト値は開発用と本番用の例。
WEB_UI_ALLOWED_ORIGINS_ENV_VAR = os.environ.get(
    "WEB_UI_ALLOWED_ORIGINS",
    "http://localhost:5000,https://your-project-id.web.app" # TODO: 実際のドメインに置き換える
)
WEB_UI_ALLOWED_ORIGINS_LIST = [
    origin.strip() for origin in WEB_UI_ALLOWED_ORIGINS_ENV_VAR.split(',')
]

generate_api_key_cors_policy = options.CorsOptions(
    cors_origins=WEB_UI_ALLOWED_ORIGINS_LIST,
    cors_methods=["get", "options"]
)



# functions/main.py の ensure_firebase_initialized 関数をさらに詳細なログ出力付きに修正

import firebase_admin
from firebase_admin import initialize_app, firestore, credentials # credentials を追加
import os
import traceback
import json # create_error_response などで使うため、トップレベルにも記述

# --- Admin SDK 初期化のためのグローバル変数 ---
db: firestore.Client | None = None # 型ヒントをより具体的に
_default_app_initialized_flag = False # initialize_app() が呼ばれたかのフラグ

def ensure_firebase_initialized():
    global db, _default_app_initialized_flag

    print(f"DEBUG: ensure_firebase_initialized: Called. Current global db is {('None' if db is None else 'SET')}, id(db): {id(db)}. App initialized flag: {_default_app_initialized_flag}")

    # 既に有効なdbクライアントがあれば何もしない
    if db is not None:
        print("INFO: ensure_firebase_initialized: db client already valid and exists. Skipping.")
        return

    # アプリがまだ初期化されていない場合のみ initialize_app() を試みる
    if not _default_app_initialized_flag:
        print("INFO: ensure_firebase_initialized: Default Firebase app not yet initialized by this function instance. Attempting initialize_app().")
        try:
            # デフォルトの認証情報を探す。環境変数 GOOGLE_APPLICATION_CREDENTIALS があればそれが使われる。
            # クラウド環境では、実行サービスアカウントの認証情報が自動的に使われるはず。
            print(f"DEBUG: ensure_firebase_initialized: GOOGLE_APPLICATION_CREDENTIALS env var is: {os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')}")
            initialize_app() # 引数なしでデフォルトアプリを初期化
            print("INFO: ensure_firebase_initialized: initialize_app() successful.")
            _default_app_initialized_flag = True # このインスタンスでは初期化が試みられたことを記録
        except ValueError as ve: # よくあるのは "The default Firebase app already exists."
            if "already exists" in str(ve).lower():
                print("INFO: ensure_firebase_initialized: Default Firebase app already existed. Using existing one.")
                _default_app_initialized_flag = True # 既存のものを使うのでフラグを立てる
            else:
                print(f"ERROR: ensure_firebase_initialized: ValueError during initialize_app(): {ve}")
                traceback.print_exc()
                db = None # 念のため
                return # 初期化失敗なのでここで抜ける
        except Exception as admin_init_err:
            print(f"ERROR: ensure_firebase_initialized: Exception during initialize_app(): {admin_init_err}")
            traceback.print_exc()
            db = None # 念のため
            return # 初期化失敗なのでここで抜ける
    else:
        print("INFO: ensure_firebase_initialized: Default Firebase app was already marked as initialized by this instance.")

    # Admin SDKアプリの初期化が成功した (または既にされていた) 場合のみ、Firestoreクライアントを取得
    if _default_app_initialized_flag:
        print("INFO: ensure_firebase_initialized: Attempting to get Firestore client...")
        try:
            temp_db_client = firestore.client() # プロジェクトIDは通常自動検出
            if temp_db_client is not None:
                db = temp_db_client # グローバル変数に代入
                print(f"INFO: ensure_firebase_initialized: Firestore client obtained successfully. Global db id: {id(db)}")
            else:
                # firestore.client() が None を返すのは非常に稀
                print("ERROR: ensure_firebase_initialized: firestore.client() returned None without raising an exception.")
                db = None
        except Exception as db_client_err:
            print(f"ERROR: ensure_firebase_initialized: Exception getting Firestore client: {db_client_err}")
            traceback.print_exc()
            db = None
    else:
        # このパスには通常到達しないはず (initialize_appでエラーになるか、フラグが立つため)
        print("WARN: ensure_firebase_initialized: _default_app_initialized_flag is False before getting client. This should not happen.")
        db = None

    if db is None:
        print("ERROR: ensure_firebase_initialized: Finished, but global db client is still None.")




# === ヘルパー関数 ===
def generate_api_key_string() -> str:
    """
    '{API_KEY_PREFIX}' プレフィックス付きのランダムなAPIキー文字列を生成します。
    """
    characters = string.ascii_letters + string.digits
    random_part = ''.join(
        random.choice(characters) for _ in range(API_KEY_RANDOM_PART_LENGTH)
    )
    return API_KEY_PREFIX + random_part

def create_error_response(message: str, status_code: int) -> https_fn.Response:
    """エラーレスポンスJSONを生成するヘルパー関数"""
    error_payload = {"error": message}
    return https_fn.Response(
        json.dumps(error_payload),
        status=status_code,
        mimetype="application/json"
    )

def create_success_response(data: dict, status_code: int = 200) -> https_fn.Response:
    """成功レスポンスJSONを生成するヘルパー関数"""
    return https_fn.Response(
        json.dumps(data),
        status=status_code,
        mimetype="application/json"
    )

# === Cloud Functions ===


@https_fn.on_request() # リージョンは後で関数ごとに指定
def helloWorld(req: https_fn.Request) -> https_fn.Response:
    ensure_firebase_initialized()
    if db is None:
        return https_fn.Response(json.dumps({"error": "DB not initialized in helloWorld"}), status=500, mimetype="application/json")
    
    # 簡単なFirestore書き込みテスト
    try:
        doc_ref = db.collection("test_from_hello").document("doc1")
        doc_ref.set({"message": "Hello World function accessed Firestore!"})
    except Exception as e:
        print(f"ERROR: Firestore write failed in helloWorld: {e}")
        # エラーでも関数自体は成功レスポンスを返す
    return https_fn.Response("Hello from Firebase in helloWorld! Firestore pinged.")

@https_fn.on_request()  # Pythonアプリからの直接呼び出しが主なのでCORSは必須ではない
def check_api_key_status(req: https_fn.Request) -> https_fn.Response:
    """
    APIキーの有効性、利用状況（残り回数など）を返します。
    この関数は利用回数のカウントアップを行いません。
    HTTPメソッド: GET
    ヘッダー: X-API-KEY (必須)
    """
    if db is None:
        print("ERROR: check_api_key_status: Firestore client not initialized.")
        return create_error_response(
            "Server configuration error: Database unavailable.", 500
        )

    print("INFO: check_api_key_status: Received request.")
    api_key = req.headers.get("X-API-KEY")

    if not api_key:
        print("WARN: check_api_key_status: API key missing in header.")
        return create_error_response("API key missing.", 401)

    api_key_short_log = (
        api_key[:len(API_KEY_PREFIX) + 3] + "..."
        if len(api_key) > (len(API_KEY_PREFIX) + 3)
        else api_key
    )
    print(
        "INFO: check_api_key_status: Verifying key starting with "
        f"{api_key_short_log}"
    )

    try:
        keys_collection_ref = db.collection("apiKeys")
        query = keys_collection_ref.where(
            filter=FieldFilter("key", "==", api_key)
        ).limit(1)
        docs = list(query.stream())

        if not docs:
            print(
                "WARN: check_api_key_status: API key not found or invalid: "
                f"{api_key_short_log}"
            )
            return create_error_response("Invalid API key.", 403)

        key_doc_snapshot = docs[0]
        key_data: dict = key_doc_snapshot.to_dict()
        doc_id = key_doc_snapshot.id

        print(
            "INFO: check_api_key_status: Found key document "
            f"{doc_id} for {api_key_short_log}"
        )

        if not key_data.get("isEnabled", False):
            print(
                "WARN: check_api_key_status: API key is disabled: "
                f"{api_key_short_log} (Doc ID: {doc_id})"
            )
            return create_error_response("API key disabled.", 403)

        usage_count: int = key_data.get("usageCount", 0)
        usage_limit: int = key_data.get("usageLimit", DEFAULT_USAGE_LIMIT)
        last_reset_timestamp: datetime | None = key_data.get("lastReset")

        now_utc = datetime.now(timezone.utc)
        effective_usage_count = usage_count

        if last_reset_timestamp:
            last_reset_dt_utc = last_reset_timestamp.replace(tzinfo=timezone.utc) \
                if last_reset_timestamp.tzinfo is None \
                else last_reset_timestamp.astimezone(timezone.utc)

            is_new_billing_month = (
                last_reset_dt_utc.year < now_utc.year or
                (last_reset_dt_utc.year == now_utc.year and \
                 last_reset_dt_utc.month < now_utc.month)
            )
            if is_new_billing_month:
                effective_usage_count = 0
                print(
                    "INFO: check_api_key_status: Key "
                    f"{api_key_short_log} is due for a monthly reset. "
                    "Effective count is 0 for this check."
                )

        remaining_usages = usage_limit - effective_usage_count
        is_limit_reached = remaining_usages <= 0

        response_data = {
            "isValid": True,
            "isEnabled": True,
            "usageCount": effective_usage_count,
            "usageLimit": usage_limit,
            "remainingUsages": max(0, remaining_usages),
            "isLimitReached": is_limit_reached,
            "lastReset": (
                last_reset_timestamp.isoformat() if last_reset_timestamp else None
            ),
        }
        print(
            "INFO: check_api_key_status: Success for "
            f"{api_key_short_log}. Status: {response_data}"
        )
        return create_success_response(response_data)

    except google_exceptions.RetryError as e:
        print(
            "ERROR: check_api_key_status: Firestore transient error for "
            f"{api_key_short_log}: {e}"
        )
        traceback.print_exc()
        return create_error_response(
            "A transient database error occurred. Please try again.", 503
        )
    except Exception as e:
        print(
            "ERROR: check_api_key_status: Unexpected error for "
            f"{api_key_short_log}: {e}"
        )
        traceback.print_exc()
        return create_error_response("Internal Server Error.", 500)

        
@https_fn.on_request() # Pythonアプリからの直接呼び出しが主なのでCORSは必須ではない
def record_api_usage(req: https_fn.Request) -> https_fn.Response:
    """
    APIキーを検証し、利用回数をインクリメントします。冪等性対応済み。
    HTTPメソッド: POST
    ヘッダー: X-API-KEY (必須)
    リクエストボディ(JSON): {"transactionId": "unique-string"} (必須)
    """
    if db is None:
        print("ERROR: record_api_usage: Firestore client not initialized.")
        return create_error_response(
            "Server configuration error: Database unavailable.", 500
        )

    print("INFO: record_api_usage: Received request.")
    api_key = req.headers.get("X-API-KEY")

    if not api_key:
        print("WARN: record_api_usage: API key missing in header.")
        return create_error_response("API key missing.", 401)

    try:
        request_body = req.get_json(silent=True)
        if request_body is None or "transactionId" not in request_body:
            print(
                "WARN: record_api_usage: Missing or invalid transactionId "
                "in request body."
            )
            return create_error_response(
                "Missing or invalid transactionId in request body.", 400
            )
        transaction_id = str(request_body["transactionId"])
        if not transaction_id:
            print("WARN: record_api_usage: Empty transactionId provided.")
            return create_error_response("transactionId cannot be empty.", 400)
    except Exception as body_parse_error:
        print(
            "ERROR: record_api_usage: Error parsing request body: "
            f"{body_parse_error}"
        )
        return create_error_response("Invalid request body format.", 400)

    api_key_short_log = (
        api_key[:len(API_KEY_PREFIX) + 3] + "..."
        if len(api_key) > (len(API_KEY_PREFIX) + 3)
        else api_key
    )
    print(
        f"INFO: record_api_usage: Attempting for key {api_key_short_log}, "
        f"transactionId: {transaction_id}"
    )

    try:
        # 冪等性チェック
        processed_txn_ref = db.collection("processedTransactions").document(transaction_id)
        processed_txn_doc = processed_txn_ref.get()

        if processed_txn_doc.exists:
            print(
                f"INFO: record_api_usage: Transaction ID {transaction_id} "
                "already processed."
            )
            processed_data = processed_txn_doc.to_dict()
            return create_success_response({
                "status": "success",
                "message": "Usage already recorded for this transactionId.",
                "recordedUsageCount": processed_data.get("recordedUsageCount", "N/A")
            })

        # APIキーの検索と事前チェック (トランザクション外)
        keys_collection_ref = db.collection("apiKeys")
        query = keys_collection_ref.where(
            filter=FieldFilter("key", "==", api_key)
        ).limit(1)
        key_docs = list(query.stream())

        if not key_docs:
            print(f"WARN: record_api_usage: API key not found: {api_key_short_log}")
            return create_error_response("Invalid API key.", 403)

        key_doc_snapshot = key_docs[0]
        key_doc_ref: DocumentReference = key_doc_snapshot.reference
        key_data_outside_txn: dict = key_doc_snapshot.to_dict()

        if not key_data_outside_txn.get("isEnabled", False):
            print(f"WARN: record_api_usage: API key is disabled: {api_key_short_log}")
            return create_error_response("API key disabled.", 403)

        # トランザクション内で利用回数を更新
        firestore_transaction: Transaction = db.transaction()
        transaction_result_container = {
            "final_usage_count": None,
            "limit_exceeded": False
        }

        @firestore.transactional
        def update_usage_in_transaction_logic(
            transaction_obj: Transaction,
            doc_ref: DocumentReference,
            result_container: dict
        ):
            snapshot = doc_ref.get(transaction=transaction_obj)
            if not snapshot.exists:
                raise google_exceptions.NotFound(
                    f"API key document {doc_ref.id} disappeared during transaction."
                )

            current_data = snapshot.to_dict()
            usage_count: int = current_data.get("usageCount", 0)
            usage_limit: int = current_data.get("usageLimit", DEFAULT_USAGE_LIMIT)
            last_reset_timestamp: datetime | None = current_data.get("lastReset")

            now_utc = datetime.now(timezone.utc)
            needs_reset = False
            if last_reset_timestamp:
                last_reset_dt_utc = last_reset_timestamp.replace(tzinfo=timezone.utc) \
                    if last_reset_timestamp.tzinfo is None \
                    else last_reset_timestamp.astimezone(timezone.utc)
                
                is_new_billing_month = (
                    last_reset_dt_utc.year < now_utc.year or
                    (last_reset_dt_utc.year == now_utc.year and \
                     last_reset_dt_utc.month < now_utc.month)
                )
                if is_new_billing_month:
                    needs_reset = True

            update_fields = {}
            if needs_reset:
                print(
                    "INFO: record_api_usage (transaction): Resetting usage for "
                    f"{doc_ref.id}"
                )
                usage_count = 0
                update_fields["usageCount"] = 0
                update_fields["lastReset"] = firestore.SERVER_TIMESTAMP

            if usage_count >= usage_limit:
                print(
                    "WARN: record_api_usage (transaction): Usage limit exceeded for "
                    f"{doc_ref.id}. Count: {usage_count}, Limit: {usage_limit}"
                )
                result_container["limit_exceeded"] = True
                return # トランザクションはコミットされるが、カウントアップはされない

            update_fields["usageCount"] = firestore.Increment(1)
            transaction_obj.update(doc_ref, update_fields)
            result_container["final_usage_count"] = usage_count + 1
            print(
                "INFO: record_api_usage (transaction): Usage count incremented for "
                f"{doc_ref.id}. New effective count: "
                f"{result_container['final_usage_count']}"
            )

        try:
            update_usage_in_transaction_logic(
                firestore_transaction, key_doc_ref, transaction_result_container
            )
        except google_exceptions.NotFound as doc_missing_err:
            print(
                "ERROR: record_api_usage: Transaction aborted, doc missing: "
                f"{doc_missing_err}"
            )
            return create_error_response(
                "Failed to update usage: key disappeared.", 500
            )
        except Exception as transaction_error:
            print(
                "ERROR: record_api_usage: Transaction failed for key "
                f"{api_key_short_log}, txnId {transaction_id}: {transaction_error}"
            )
            traceback.print_exc()
            return create_error_response(
                "Failed to update usage count due to a server error.", 500
            )

        if transaction_result_container["limit_exceeded"]:
            return create_error_response("Usage limit exceeded.", 429)

        if transaction_result_container["final_usage_count"] is not None:
            expires_at = datetime.now(timezone.utc) + \
                         timedelta(days=PROCESSED_TRANSACTION_TTL_DAYS)
            processed_txn_ref.set({
                "processedAt": firestore.SERVER_TIMESTAMP,
                "apiKeyIdentifier": api_key_short_log,
                "recordedUsageCount": transaction_result_container["final_usage_count"],
                "expiresAt": expires_at
            })
            print(
                "INFO: record_api_usage: Successfully recorded usage for key "
                f"{api_key_short_log}, txnId {transaction_id}. Effective count: "
                f"{transaction_result_container['final_usage_count']}"
            )
            return create_success_response({
                "status": "success",
                "message": "Usage recorded successfully.",
                "newEffectiveUsageCount": transaction_result_container["final_usage_count"]
            })
        else:
            # このパスは、上限超過でもなく、カウントアップもされなかった場合
            # (通常、トランザクション内で limit_exceeded=True になるはず)
            print(
                "ERROR: record_api_usage: Transaction for "
                f"{api_key_short_log}, txnId {transaction_id} "
                "finished without updating count or signaling known limit."
            )
            return create_error_response(
                "Internal server error during usage update processing.", 500
            )

    except google_exceptions.RetryError as e:
        print(
            "ERROR: record_api_usage: Firestore transient error for "
            f"{api_key_short_log}, txnId {transaction_id}: {e}"
        )
        traceback.print_exc()
        return create_error_response(
            "A transient database error occurred. Please try again.", 503
        )
    except Exception as e:
        print(
            "ERROR: record_api_usage: Unexpected error for "
            f"{api_key_short_log}, txnId {transaction_id}: {e}"
        )
        traceback.print_exc()
        return create_error_response("Internal Server Error.", 500)


@https_fn.on_request(cors=generate_api_key_cors_policy)
def generate_or_fetch_api_key(req: https_fn.Request) -> https_fn.Response:
    """
    IDトークンでユーザーを認証し、有効なAPIキーを返します。
    キーが存在しない場合は新しく生成して保存してから返します。
    """
    ensure_firebase_initialized() # ★★★ この行を追加 ★★★
    if db is None:
        print(
            "ERROR: generate_or_fetch_api_key: Firestore client not initialized."
        )
        return create_error_response(
            "Server configuration error: Database unavailable.", 500
        )

    print("INFO: generate_or_fetch_api_key: Received request.")
    auth_header = req.headers.get("Authorization")
    id_token: str | None = None

    if auth_header and auth_header.startswith("Bearer "):
        id_token = auth_header.split("Bearer ", 1)[1]

    if not id_token:
        print(
            "WARN: generate_or_fetch_api_key: Authorization header missing "
            "or invalid format."
        )
        return create_error_response(
            "Unauthorized: Missing or invalid token.", 401
        )

    try:
        try:
            decoded_token = auth.verify_id_token(id_token)
        except auth.RevokedIdTokenError:
            print(
                "WARN: generate_or_fetch_api_key: ID token has been revoked."
            )
            return create_error_response("Unauthorized: Token revoked.", 401)
        except auth.UserDisabledError:
            print(
                "WARN: generate_or_fetch_api_key: User account is disabled."
            )
            return create_error_response("Unauthorized: User disabled.", 401)
        except auth.InvalidIdTokenError as token_error:
            print(
                "WARN: generate_or_fetch_api_key: Invalid ID token: "
                f"{token_error}"
            )
            return create_error_response(
                f"Unauthorized: Invalid token ({token_error}).", 401
            )
        except Exception as auth_verify_error:
            print(
                "ERROR: generate_or_fetch_api_key: Token verification failed "
                f"with unexpected auth error: {auth_verify_error}"
            )
            traceback.print_exc()
            return create_error_response(
                "Unauthorized: Token verification failed.", 401
            )

        uid: str | None = decoded_token.get('uid')
        email: str = decoded_token.get('email', '')

        if not uid:
            print(
                "ERROR: generate_or_fetch_api_key: UID not found in a "
                "valid token."
            )
            return create_error_response(
                "Unauthorized: Invalid token claims (UID missing).", 401
            )

        print(
            "INFO: generate_or_fetch_api_key: Verified user. "
            f"UID='{uid}', Email='{email}'"
        )

        keys_collection_ref = db.collection("apiKeys")
        query = keys_collection_ref.where(
            filter=FieldFilter("user_uid", "==", uid)
        ).where(
            filter=FieldFilter("isEnabled", "==", True)
        ).limit(1)
        active_key_docs = list(query.stream())

        if active_key_docs:
            existing_key_data = active_key_docs[0].to_dict()
            api_key_value = existing_key_data.get("key")
            doc_id = active_key_docs[0].id

            if not api_key_value:
                print(
                    "ERROR: generate_or_fetch_api_key: Data inconsistency - "
                    f"Document {doc_id} for user {uid} is missing 'key' field."
                )
                return create_error_response(
                    "Internal Server Error: Key data inconsistency.", 500
                )

            api_key_short_log = api_key_value[:len(API_KEY_PREFIX) + 3] + "..."
            print(
                "INFO: generate_or_fetch_api_key: Found existing active API key "
                f"for user {uid}: {api_key_short_log}"
            )
            return https_fn.Response(
                api_key_value, status=200, content_type="text/plain"
            )
        else:
            print(
                "INFO: generate_or_fetch_api_key: No active API key found for "
                f"user {uid}. Generating new one."
            )
            new_api_key_str = generate_api_key_string()
            api_key_short_log = new_api_key_str[:len(API_KEY_PREFIX) + 3] + "..."
            current_timestamp = firestore.SERVER_TIMESTAMP

            new_key_document_data = {
                "key": new_api_key_str,
                "user_uid": uid,
                "isEnabled": True,
                "usageCount": 0,
                "usageLimit": DEFAULT_USAGE_LIMIT,
                "lastReset": current_timestamp,
                "created_at": current_timestamp,
                "ownerEmail": email or "",
            }
            new_doc_ref: DocumentReference = keys_collection_ref.document()
            try:
                new_doc_ref.set(new_key_document_data)
                print(
                    "INFO: generate_or_fetch_api_key: Successfully saved new "
                    f"API key for user {uid}: {api_key_short_log} "
                    f"(Doc ID: {new_doc_ref.id})"
                )
                return https_fn.Response(
                    new_api_key_str, status=201, content_type="text/plain"
                )
            except Exception as db_write_err:
                print(
                    "ERROR: generate_or_fetch_api_key: Failed to save new API "
                    f"key to Firestore for user {uid}: {db_write_err}"
                )
                traceback.print_exc()
                return create_error_response(
                    "Internal Server Error: Could not save new API key.", 500
                )

    except google_exceptions.RetryError as e:
        print(
            "ERROR: generate_or_fetch_api_key: Firestore transient error: {e}"
        )
        traceback.print_exc()
        return create_error_response(
            "A transient database error occurred. Please try again.", 503
        )
    except Exception as e:
        print(
            "ERROR: generate_or_fetch_api_key: An unexpected critical error "
            f"occurred: {e}"
        )
        traceback.print_exc()
        return create_error_response("Internal Server Error.", 500)

@https_fn.on_request()
def verify_api_key(req: https_fn.Request) -> https_fn.Response:
    """
    APIキーを検証し、利用回数をチェック・カウントアップするHTTP関数。
    """
    initialize_services_if_needed()

    if db is None:
        print("Error in verify_api_key: Firestore client not initialized or unavailable.")
        return https_fn.Response(
            "Server configuration error: Firestore unavailable.",
            status=500
        )

    print("verify_api_key: Received request.")

    # 1. リクエストヘッダーからAPIキーを取得
    api_key = req.headers.get("X-API-KEY") # ヘッダー名は実際の仕様に合わせる

    # APIキーが存在しない場合
    if not api_key:
        print("verify_api_key: Error - API key not provided in header.")
        return https_fn.Response("Unauthorized: API key missing.", status=401)

    # ログ出力用にキーを短縮 (セキュリティ配慮)
    if len(api_key) > 5:
        api_key_short = api_key[:5] + "..."
    else:
        api_key_short = api_key
    print(f"verify_api_key: Attempting to verify key starting with {api_key_short}")

    try:
        # 2. FirestoreでAPIキーを検索
        keys_ref = db.collection("apiKeys")
        # keyフィールドで完全一致、結果は最大1件
        query = keys_ref.where(field_path="key", op_string="==", value=api_key).limit(1)
        # クエリを実行し、結果を取得
        docs = list(query.stream())

        # 3. キーの存在確認
        if not docs:
            # ドキュメントが見つからない場合
            print(f"verify_api_key: Error - API key not found or invalid: {api_key_short}")
            # 存在しないキーなので 403 Forbidden を返す
            return https_fn.Response("Unauthorized: Invalid API key.", status=403)

        # キーが見つかった場合、最初のドキュメントを使用
        key_doc_snapshot = docs[0]
        key_doc_ref: DocumentReference = key_doc_snapshot.reference
        key_data: dict = key_doc_snapshot.to_dict()
        doc_id = key_doc_snapshot.id

        print(f"verify_api_key: Found key document {doc_id} for {api_key_short}")

        # 4. キーの有効性 (isEnabled) チェック
        # isEnabledフィールドが存在しない、またはFalseの場合は無効とみなす
        if not key_data.get("isEnabled", False):
            print(f"verify_api_key: Error - API key is disabled: {api_key_short} (Doc ID: {doc_id})")
            return https_fn.Response("Unauthorized: API key disabled.", status=403)

        # 5. 利用回数チェックと更新 (トランザクション処理)
        # トランザクション内で実行される関数を定義
        @_firestore.transactional  # エイリアス経由で使用
        def check_and_update_usage_transaction(
            transaction,  # トランザクションオブジェクト (型ヒントは不要)
            doc_ref_in_transaction: DocumentReference
        ) -> bool | None:  # 成功時はTrue, 上限超過はNone
            """トランザクション内で利用状況を確認し、更新する (アトミック処理)"""
            try:
                # トランザクション内で最新のドキュメントデータを取得
                snapshot = doc_ref_in_transaction.get(transaction=transaction)

                # ドキュメントが存在しない場合 (トランザクション中に削除されたなど)
                if not snapshot.exists:
                    print(f"verify_api_key: Error in transaction - Doc {doc_ref_in_transaction.id} deleted during transaction.")
                    # 上限超過と同様にNoneを返して処理を中断
                    return None

                current_data = snapshot.to_dict()

                # 各フィールドを取得 (存在しない場合のデフォルト値も設定)
                usage_count: int = current_data.get("usageCount", 0)
                usage_limit: int = current_data.get("usageLimit", 100)  # デフォルト100回
                last_reset_timestamp: datetime | None = current_data.get("lastReset")

                needs_reset = False
                now_utc = datetime.now(timezone.utc)

                # 最終リセット日時が存在する場合、月が変わったかチェック
                if last_reset_timestamp:
                    # Firestore TimestampはUTCとして扱う
                    last_reset_dt_utc = last_reset_timestamp.replace(tzinfo=timezone.utc)
                    if (last_reset_dt_utc.year < now_utc.year or
                            last_reset_dt_utc.month < now_utc.month):
                        needs_reset = True

                # 月が変わっていた場合、カウンターをリセット
                if needs_reset:
                    print(f"verify_api_key: Resetting usage count for key {api_key_short} (Doc ID: {snapshot.id})")
                    usage_count = 0  # カウントをリセット
                    reset_update_data = {
                        "usageCount": 0,
                        "lastReset": _firestore.SERVER_TIMESTAMP  # エイリアス経由で使用
                    }
                    transaction.update(doc_ref_in_transaction, reset_update_data)
                    print("verify_api_key: Usage count reset in transaction.")

                # 上限チェック (リセット後のカウントで比較)
                if usage_count >= usage_limit:
                    print(f"verify_api_key: Error - Usage limit exceeded for key {api_key_short}. "
                          f"Count: {usage_count}, Limit: {usage_limit}")
                    # 上限超過を示す None を返す
                    return None

                # 上限未満の場合、カウントをインクリメント
                print(f"verify_api_key: Incrementing usage count for key {api_key_short}. "
                      f"Previous: {usage_count}")
                increment_update_data = {
                    # _firestore.Increment でアトミックに+1する
                    "usageCount": _firestore.Increment(1)  # エイリアス経由で使用
                }
                transaction.update(doc_ref_in_transaction, increment_update_data)
                print("verify_api_key: Usage count incremented in transaction.")

                # 利用許可を示す True を返す
                return True

            except Exception as trans_error:
                # トランザクション内での予期せぬエラー
                print(f"verify_api_key: Error inside usage check transaction for {doc_ref_in_transaction.id}: {trans_error}")
                # エラーを再送出してトランザクションを失敗させる
                raise trans_error

        # --- トランザクションの実行 ---
        try:
            # トランザクションオブジェクトを作成
            transaction_obj = db.transaction()
            # 定義したトランザクション関数を実行
            update_result = check_and_update_usage_transaction(transaction_obj, key_doc_ref)
        except Exception as transaction_execution_error:
            # トランザクション自体の実行時エラー (内部でraiseされたエラー含む)
            print(f"verify_api_key: Transaction execution failed for key {api_key_short}: {transaction_execution_error}")
            traceback.print_exc()
            return https_fn.Response("Server error during usage update.", status=500)

        # 6. トランザクション結果に基づきレスポンスを返す
        if update_result is True:
            # 正常に利用回数がインクリメントされた場合
            owner_uid = key_data.get("user_uid", "unknown")
            print(f"verify_api_key: Success for key {api_key_short}. Owner UID: {owner_uid}")

            # ★★★ 本来のAPI処理をここに追加 ★★★
            # 例: データベース検索、計算、外部API呼び出しなど
            # api_result = perform_actual_api_work(key_data, req) # 仮の関数呼び出し

            # 仮の成功レスポンス
            return https_fn.Response(f"API key verified successfully for user {owner_uid}!")
        elif update_result is None:
            # 上限に達していた場合
            # ステータスコード 429 Too Many Requests がより適切
            return https_fn.Response("Forbidden: Usage limit exceeded.", status=429)
        else:
            # トランザクション関数が予期せず False などを返した場合 (通常は起こらないはず)
            print(f"verify_api_key: Unexpected result ({update_result}) from transaction for key {api_key_short}.")
            return https_fn.Response("Server error: Unexpected transaction result.", status=500)

    # 7. 関数全体の例外処理
    except google_exceptions.NotFound as e:
        # Firestoreの検索自体で問題があった場合など (例: コレクションが存在しない - 通常考えにくい)
        print(f"verify_api_key: Firestore NotFound Error during query: {e}")
        # 403を返すのが適切か、あるいは500か要検討
        return https_fn.Response("Unauthorized: Invalid API key.", status=403)
    except google_exceptions.PermissionDenied as e:
        # Firestoreへのアクセス権限がない場合 (Admin SDKでは通常考えにくい)
        print(f"verify_api_key: Firestore Permission Denied Error: {e}")
        return https_fn.Response("Server configuration error (permissions).", status=500)
    except Exception as e:
        # その他の予期せぬエラー全般
        print(f"verify_api_key: An unexpected critical error occurred: {e}")
        traceback.print_exc()  # 完全なスタックトレースを出力
        return https_fn.Response("Internal Server Error", status=500)
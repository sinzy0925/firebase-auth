# functions/main.py

# --- 標準ライブラリ ---
import os
import uuid  # transactionId 生成の候補として (今回は未使用だが一般的に使われる)
import secrets  # APIキー生成用
from datetime import datetime, timezone, timedelta
import traceback
import json
import logging  # Python標準のロギング

# --- Firebase Admin SDK & Cloud Functions ---
import firebase_admin
from firebase_admin import initialize_app, firestore, auth, credentials
from firebase_functions import https_fn, options

# --- Google Cloud Libraries ---
from google.cloud.firestore_v1.client import Client as FirestoreClient  # 型ヒント用
from google.cloud.firestore_v1.document import DocumentReference
from google.cloud.firestore_v1.transaction import Transaction
from google.cloud.firestore_v1.base_query import FieldFilter
from google.api_core import exceptions as google_exceptions

# === ロガー設定 ===
# 環境変数 DEBUG_FUNCTIONS が "true" の場合にデバッグレベルのログを出力
DEBUG_MODE = os.environ.get("DEBUG_FUNCTIONS", "false").lower() == "true"
log_level = logging.DEBUG if DEBUG_MODE else logging.INFO
logging.basicConfig(level=log_level, format='%(levelname)s: %(asctime)s: %(message)s')
logger = logging.getLogger(__name__)


# === 全体的なオプション設定 ===
options.set_global_options(region=options.SupportedRegion.ASIA_NORTHEAST1)


# === 定数 ===
DEFAULT_USAGE_LIMIT = 100
PROCESSED_TRANSACTION_TTL_DAYS = 1
API_KEY_PREFIX = "sk_"

# === CORS設定値の定義 (generate_or_fetch_api_key 用) ===
WEB_UI_ALLOWED_ORIGINS_ENV_VAR = os.environ.get(
    "WEB_UI_ALLOWED_ORIGINS",
    "https://your-project-id.web.app"  # 本番環境ではFirebase Functionsの環境変数で設定
)
WEB_UI_ALLOWED_ORIGINS_LIST = [
    origin.strip() for origin in WEB_UI_ALLOWED_ORIGINS_ENV_VAR.split(',') if origin.strip()
]
generate_api_key_cors_policy = options.CorsOptions(
    cors_origins=WEB_UI_ALLOWED_ORIGINS_LIST,
    cors_methods=["get", "options"]
)


# === Admin SDK 初期化 ===
_default_app_initialized_flag = False
db: FirestoreClient | None = None


def ensure_firebase_initialized():
    """
    Firebase Admin SDKとFirestoreクライアントが初期化されていることを確認します。
    """
    global db, _default_app_initialized_flag

    if db is not None and _default_app_initialized_flag:
        logger.debug("ensure_firebase_initialized: Firestore client and app already initialized.")
        return

    if not _default_app_initialized_flag:
        logger.info("ensure_firebase_initialized: Default Firebase app not yet initialized. Attempting initialize_app().")
        try:
            logger.debug(f"GOOGLE_APPLICATION_CREDENTIALS: {os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')}")
            initialize_app()
            _default_app_initialized_flag = True
            logger.info("ensure_firebase_initialized: initialize_app() successful.")
        except ValueError as ve:
            if "already exists" in str(ve).lower():
                _default_app_initialized_flag = True
                logger.info("ensure_firebase_initialized: Default Firebase app already existed. Using existing one.")
            else:
                logger.error(f"ensure_firebase_initialized: ValueError during initialize_app(): {ve}", exc_info=DEBUG_MODE)
                db = None
                return
        except Exception as admin_init_err:
            logger.error(f"ensure_firebase_initialized: Exception during initialize_app(): {admin_init_err}", exc_info=DEBUG_MODE)
            db = None
            return
    else:
        logger.debug("ensure_firebase_initialized: Default Firebase app was already marked as initialized.")

    if _default_app_initialized_flag and db is None:
        logger.info("ensure_firebase_initialized: Attempting to get Firestore client.")
        try:
            temp_db_client = firestore.client()
            if temp_db_client:
                logger.debug(f"ensure_firebase_initialized: firestore.client() returned object of type: {type(temp_db_client)}")
                db = temp_db_client
                logger.info("ensure_firebase_initialized: Firestore client obtained successfully.")
            else:
                logger.error("ensure_firebase_initialized: firestore.client() returned None.")
                db = None
        except Exception as db_client_err:
            logger.error(f"ensure_firebase_initialized: Exception getting Firestore client: {db_client_err}", exc_info=DEBUG_MODE)
            db = None

    if db is None and _default_app_initialized_flag:
        logger.error("ensure_firebase_initialized: Finished, but global db client is None despite app initialization.")
    elif db is not None:
        logger.debug("ensure_firebase_initialized: Finished. Global db client is SET.")


# === ヘルパー関数 ===

def generate_api_key_string() -> str:
    """
    '{API_KEY_PREFIX}' プレフィックス付きの暗号学的に安全なAPIキー文字列を生成します。
    """
    random_part = secrets.token_urlsafe(32)
    return API_KEY_PREFIX + random_part


def create_error_response(
        internal_message: str,
        public_message: str,
        status_code: int,
        log_exception: bool = False
) -> https_fn.Response:
    """エラーレスポンスJSONを生成するヘルパー関数"""
    logger.error(f"Error: {internal_message}" + (f" - Status: {status_code}" if status_code else ""))
    if log_exception and DEBUG_MODE:
        traceback.print_exc()

    error_payload = {"error": public_message}
    return https_fn.Response(
        json.dumps(error_payload),
        status=status_code,
        mimetype="application/json"
    )


def create_success_response(
        data: dict | str,
        status_code: int = 200,
        content_type: str = "application/json"
) -> https_fn.Response:
    """成功レスポンスJSONまたはプレーンテキストを生成するヘルパー関数"""
    response_body = json.dumps(data) if content_type == "application/json" else data
    return https_fn.Response(
        response_body,
        status=status_code,
        mimetype=content_type
    )


# === Cloud Functions ===

@https_fn.on_request()
def helloWorld(req: https_fn.Request) -> https_fn.Response:
    """
    動作確認用のシンプルな関数。Firestoreへの書き込みを試みます。
    """
    ensure_firebase_initialized()
    if db is None:
        return create_error_response(
            internal_message="helloWorld: DB not initialized.",
            public_message="Server configuration error.",
            status_code=500
        )

    try:
        doc_ref = db.collection("test_from_hello").document("doc1")
        doc_ref.set({
            "message": "Hello World function accessed Firestore!",
            "timestamp": firestore.SERVER_TIMESTAMP
        })
        logger.info("helloWorld: Firestore write successful.")
    except Exception as e:
        logger.error(f"helloWorld: Firestore write failed: {e}", exc_info=DEBUG_MODE)
    return create_success_response(
        data="Hello from Firebase in helloWorld! Firestore pinged.",
        content_type="text/plain"
    )


@https_fn.on_request()
def verify_api_key(req: https_fn.Request) -> https_fn.Response:
    """
    【既存アプリ用】APIキーを検証し、利用回数をチェック・カウントアップする。
    注意: この関数は冪等性を持ちません。複数回呼び出されると都度カウントアップされます。
    HTTPメソッド: (GETまたはPOSTを想定)
    ヘッダー: X-API-KEY (必須)
    """
    ensure_firebase_initialized()
    if db is None:
        return create_error_response(
            internal_message="verify_api_key: Firestore client not initialized.",
            public_message="Server configuration error.",
            status_code=500
        )

    logger.info("verify_api_key: Received request (for existing apps).")
    api_key = req.headers.get("X-API-KEY")

    if not api_key:
        logger.warning("verify_api_key: API key missing in header.")
        return create_error_response(
            internal_message="API key missing in header.",
            public_message="API key missing.",
            status_code=401
        )

    api_key_short_log = api_key[:len(API_KEY_PREFIX) + 3] + "..." if len(api_key) > (len(API_KEY_PREFIX) + 3) else api_key
    logger.info(f"verify_api_key: Attempting to verify and increment for key {api_key_short_log}")

    try:
        keys_collection_ref = db.collection("apiKeys")
        query = keys_collection_ref.where(filter=FieldFilter("key", "==", api_key)).limit(1)
        docs = list(query.stream())

        if not docs:
            logger.warning(f"verify_api_key: API key not found: {api_key_short_log}")
            return create_error_response(
                internal_message=f"API key not found: {api_key_short_log}",
                public_message="Invalid API key.",
                status_code=403
            )

        key_doc_snapshot = docs[0]
        key_doc_ref: DocumentReference = key_doc_snapshot.reference
        key_data: dict = key_doc_snapshot.to_dict()
        if key_data is None:
            logger.error(f"verify_api_key: Document {key_doc_snapshot.id} has no data for key {api_key_short_log}.")
            return create_error_response(
                internal_message=f"Document {key_doc_snapshot.id} has no data for key {api_key_short_log}.",
                public_message="API key data corrupted.",
                status_code=500
            )
        doc_id = key_doc_snapshot.id

        if not key_data.get("isEnabled", False):
            logger.warning(f"verify_api_key: API key is disabled: {api_key_short_log} (Doc ID: {doc_id})")
            return create_error_response(
                internal_message=f"API key disabled: {api_key_short_log}",
                public_message="API key disabled.",
                status_code=403
            )

        firestore_transaction: Transaction = db.transaction()
        transaction_result_container = {
            "updated": False,
            "limit_exceeded": False,
            "owner_uid": key_data.get("user_uid", "unknown")
        }

        @firestore.transactional
        def check_and_update_usage_in_transaction(
            transaction_obj: Transaction,
            doc_ref_in_transaction: DocumentReference,
            result_container: dict
        ):
            snapshot = doc_ref_in_transaction.get(transaction=transaction_obj)
            if not snapshot.exists:
                raise google_exceptions.NotFound(
                    f"API key document {doc_ref_in_transaction.id} disappeared during transaction."
                )

            current_data = snapshot.to_dict()
            if current_data is None:
                raise ValueError(f"Document {snapshot.id} data is unexpectedly None in transaction.")

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
                    (last_reset_dt_utc.year == now_utc.year and last_reset_dt_utc.month < now_utc.month)
                )
                if is_new_billing_month:
                    needs_reset = True

            if needs_reset:
                logger.info(f"verify_api_key (transaction): Resetting usage for {doc_ref_in_transaction.id}")
                # リセットして、今回の使用分(1)をカウントする
                update_data = {
                    "usageCount": 1,
                    "lastReset": firestore.SERVER_TIMESTAMP
                }
                transaction_obj.update(doc_ref_in_transaction, update_data)
                result_container["updated"] = True
                logger.info(f"verify_api_key (transaction): Usage count reset and set to 1 for {doc_ref_in_transaction.id}.")
            else:  # 月替わりリセットが不要な場合
                if usage_count >= usage_limit:
                    logger.warning(
                        f"verify_api_key (transaction): Usage limit exceeded for {doc_ref_in_transaction.id}. "
                        f"Count: {usage_count}, Limit: {usage_limit}"
                    )
                    result_container["limit_exceeded"] = True
                    return

                # 既存のカウントをインクリメント
                update_data = {"usageCount": firestore.Increment(1)}
                transaction_obj.update(doc_ref_in_transaction, update_data)
                result_container["updated"] = True
                logger.info(
                    f"verify_api_key (transaction): Usage count incremented for {doc_ref_in_transaction.id}. "
                    f"New count will be {usage_count + 1}"
                )

        try:
            check_and_update_usage_in_transaction(
                firestore_transaction, key_doc_ref, transaction_result_container
            )
        except google_exceptions.NotFound as doc_missing_err:
            return create_error_response(
                internal_message=f"verify_api_key: Transaction aborted, key {api_key_short_log} disappeared: {doc_missing_err}",
                public_message="Failed to update usage: API key may have been deleted.",
                status_code=500,
                log_exception=True
            )
        except Exception as transaction_error:
            return create_error_response(
                internal_message=f"verify_api_key: Transaction failed for key {api_key_short_log}: {transaction_error}",
                public_message="Failed to update usage count due to a server error.",
                status_code=500,
                log_exception=True
            )

        if transaction_result_container["limit_exceeded"]:
            return create_error_response(
                internal_message=f"Usage limit exceeded for key {api_key_short_log}.",
                public_message="Usage limit exceeded.",
                status_code=429  # Too Many Requests
            )

        if transaction_result_container["updated"]:
            logger.info(
                f"verify_api_key: Success, usage incremented for key {api_key_short_log}. "
                f"Owner UID: {transaction_result_container['owner_uid']}"
            )
            return create_success_response(
                data={"message": f"API key verified and usage recorded for user {transaction_result_container['owner_uid']}"}
            )
        else:
            # このパスはロジック修正により到達しにくくなったはずだが、念のため残す
            logger.error(
                f"verify_api_key: Transaction for {api_key_short_log} finished unexpectedly "
                "(not updated, not limit exceeded)."
            )
            return create_error_response(
                internal_message=f"verify_api_key: Transaction for key {api_key_short_log} finished unexpectedly.",
                public_message="Internal server error during usage update processing.",
                status_code=500
            )

    except google_exceptions.RetryError as e:
        return create_error_response(
            internal_message=f"verify_api_key: Firestore transient error for {api_key_short_log}: {e}",
            public_message="A transient database error occurred. Please try again.",
            status_code=503,
            log_exception=True
        )
    except Exception as e:
        return create_error_response(
            internal_message=f"verify_api_key: Unexpected error for {api_key_short_log}: {e}",
            public_message="Internal Server Error.",
            status_code=500,
            log_exception=True
        )


@https_fn.on_request()
def check_api_key_status(req: https_fn.Request) -> https_fn.Response:
    """
    APIキーの有効性、利用状況（残り回数など）を返します。
    この関数は利用回数のカウントアップを行いません。
    """
    ensure_firebase_initialized()
    if db is None:
        return create_error_response(
            internal_message="check_api_key_status: Firestore client not initialized.",
            public_message="Server configuration error.",
            status_code=500
        )

    logger.info("check_api_key_status: Received request.")
    api_key = req.headers.get("X-API-KEY")

    if not api_key:
        logger.warning("check_api_key_status: API key missing in header.")
        return create_error_response(
            internal_message="API key missing in header.",
            public_message="API key missing.",
            status_code=401
        )

    api_key_short_log = api_key[:len(API_KEY_PREFIX) + 3] + "..." if len(api_key) > (len(API_KEY_PREFIX) + 3) else api_key
    logger.info(f"check_api_key_status: Verifying key starting with {api_key_short_log}")

    try:
        keys_collection_ref = db.collection("apiKeys")
        query = keys_collection_ref.where(filter=FieldFilter("key", "==", api_key)).limit(1)
        docs = list(query.stream())

        if not docs:
            logger.warning(f"check_api_key_status: API key not found or invalid: {api_key_short_log}")
            return create_error_response(
                internal_message=f"API key not found: {api_key_short_log}",
                public_message="Invalid API key.",
                status_code=403
            )

        key_doc_snapshot = docs[0]
        key_data: dict | None = key_doc_snapshot.to_dict()
        if key_data is None:
            logger.error(f"check_api_key_status: Document {key_doc_snapshot.id} has no data for key {api_key_short_log}.")
            return create_error_response(
                internal_message=f"Document {key_doc_snapshot.id} has no data for key {api_key_short_log}.",
                public_message="API key data corrupted.",
                status_code=500
            )
        doc_id = key_doc_snapshot.id
        logger.info(f"check_api_key_status: Found key document {doc_id} for {api_key_short_log}")

        if not key_data.get("isEnabled", False):
            logger.warning(f"check_api_key_status: API key is disabled: {api_key_short_log} (Doc ID: {doc_id})")
            return create_error_response(
                internal_message=f"API key disabled: {api_key_short_log}",
                public_message="API key disabled.",
                status_code=403
            )

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
                (last_reset_dt_utc.year == now_utc.year and last_reset_dt_utc.month < now_utc.month)
            )
            if is_new_billing_month:
                effective_usage_count = 0
                logger.info(
                    f"check_api_key_status: Key {api_key_short_log} is due for a monthly reset. "
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
            "lastReset": (last_reset_timestamp.isoformat() if last_reset_timestamp else None),
        }
        logger.info(f"check_api_key_status: Success for {api_key_short_log}. Status: {response_data}")
        return create_success_response(data=response_data)

    except google_exceptions.RetryError as e:
        return create_error_response(
            internal_message=f"check_api_key_status: Firestore transient error for {api_key_short_log}: {e}",
            public_message="A transient database error occurred. Please try again.",
            status_code=503,
            log_exception=True
        )
    except Exception as e:
        return create_error_response(
            internal_message=f"check_api_key_status: Unexpected error for {api_key_short_log}: {e}",
            public_message="Internal Server Error.",
            status_code=500,
            log_exception=True
        )


@https_fn.on_request()
def record_api_usage(req: https_fn.Request) -> https_fn.Response:
    """
    APIキーを検証し、利用回数をインクリメントします。冪等性対応済み。
    """
    ensure_firebase_initialized()
    if db is None:
        return create_error_response(
            internal_message="record_api_usage: Firestore client not initialized.",
            public_message="Server configuration error.",
            status_code=500
        )

    logger.info("record_api_usage: Received request.")
    api_key = req.headers.get("X-API-KEY")

    if not api_key:
        logger.warning("record_api_usage: API key missing in header.")
        return create_error_response(
            internal_message="API key missing in header.",
            public_message="API key missing.",
            status_code=401
        )

    try:
        request_body = req.get_json(silent=True)
        if request_body is None or "transactionId" not in request_body:
            logger.warning("record_api_usage: Missing or invalid transactionId in request body.")
            return create_error_response(
                internal_message="Missing or invalid transactionId in request body.",
                public_message="Missing or invalid transactionId.",
                status_code=400
            )
        transaction_id = str(request_body["transactionId"]).strip()
        if not transaction_id:
            logger.warning("record_api_usage: Empty transactionId provided.")
            return create_error_response(
                internal_message="Empty transactionId provided.",
                public_message="transactionId cannot be empty.",
                status_code=400
            )
    except Exception as body_parse_error:
        return create_error_response(
            internal_message=f"record_api_usage: Error parsing request body: {body_parse_error}",
            public_message="Invalid request body format.",
            status_code=400,
            log_exception=True
        )

    api_key_short_log = api_key[:len(API_KEY_PREFIX) + 3] + "..." if len(api_key) > (len(API_KEY_PREFIX) + 3) else api_key
    logger.info(f"record_api_usage: Attempting for key {api_key_short_log}, transactionId: {transaction_id}")

    try:
        processed_txn_ref = db.collection("processedTransactions").document(transaction_id)
        processed_txn_doc = processed_txn_ref.get()

        if processed_txn_doc.exists:
            logger.info(f"record_api_usage: Transaction ID {transaction_id} already processed.")
            processed_data = processed_txn_doc.to_dict()
            if processed_data is None:
                processed_data = {}
            return create_success_response(data={
                "status": "success",
                "message": "Usage already recorded for this transactionId.",
                "recordedUsageCount": processed_data.get("recordedUsageCount", "N/A")
            })

        keys_collection_ref = db.collection("apiKeys")
        query = keys_collection_ref.where(filter=FieldFilter("key", "==", api_key)).limit(1)
        key_docs = list(query.stream())

        if not key_docs:
            logger.warning(f"record_api_usage: API key not found: {api_key_short_log}")
            return create_error_response(
                internal_message=f"API key not found: {api_key_short_log}",
                public_message="Invalid API key.",
                status_code=403
            )

        key_doc_snapshot = key_docs[0]
        key_doc_ref: DocumentReference = key_doc_snapshot.reference
        key_data_outside_txn: dict | None = key_doc_snapshot.to_dict()
        if key_data_outside_txn is None:
            logger.error(f"record_api_usage: API key document {key_doc_snapshot.id} has no data (outside txn).")
            return create_error_response(
                internal_message=f"API key document {key_doc_snapshot.id} has no data (outside txn).",
                public_message="API key data corrupted.",
                status_code=500
            )

        if not key_data_outside_txn.get("isEnabled", False):
            logger.warning(f"record_api_usage: API key is disabled: {api_key_short_log}")
            return create_error_response(
                internal_message=f"API key disabled: {api_key_short_log}",
                public_message="API key disabled.",
                status_code=403
            )

        firestore_transaction: Transaction = db.transaction()
        transaction_result_container = {
            "final_usage_count": None,
            "limit_exceeded_in_txn": False,
            "was_reset_in_txn": False
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
            if current_data is None:
                raise ValueError(f"Document {snapshot.id} data is unexpectedly None in transaction.")

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
                    (last_reset_dt_utc.year == now_utc.year and last_reset_dt_utc.month < now_utc.month)
                )
                if is_new_billing_month:
                    needs_reset = True

            if needs_reset:
                logger.info(f"record_api_usage (transaction): Resetting usage for {doc_ref.id}")
                result_container["was_reset_in_txn"] = True
                # リセットして、今回の使用分(1)をカウントする
                update_data = {
                    "usageCount": 1,
                    "lastReset": firestore.SERVER_TIMESTAMP
                }
                transaction_obj.update(doc_ref, update_data)
                result_container["final_usage_count"] = 1
                logger.info(f"record_api_usage (transaction): Usage count reset and set to 1 for {doc_ref.id}.")
            else:  # 月替わりリセットが不要な場合
                if usage_count >= usage_limit:
                    logger.warning(
                        f"record_api_usage (transaction): Usage limit exceeded for {doc_ref.id}. "
                        f"Count: {usage_count}, Limit: {usage_limit}"
                    )
                    result_container["limit_exceeded_in_txn"] = True
                    return

                # 既存のカウントをインクリメント
                update_data = {"usageCount": firestore.Increment(1)}
                transaction_obj.update(doc_ref, update_data)

                result_container["final_usage_count"] = usage_count + 1
                logger.info(
                    f"record_api_usage (transaction): Usage count incremented for {doc_ref.id}. "
                    f"New effective count: {result_container['final_usage_count']}"
                )

        try:
            update_usage_in_transaction_logic(
                firestore_transaction, key_doc_ref, transaction_result_container
            )
        except google_exceptions.NotFound as doc_missing_err:
            return create_error_response(
                internal_message=f"record_api_usage: Transaction aborted, key {api_key_short_log} disappeared: {doc_missing_err}",
                public_message="Failed to update usage: API key may have been deleted.",
                status_code=500,
                log_exception=True
            )
        except Exception as transaction_error:
            return create_error_response(
                internal_message=f"record_api_usage: Transaction failed for key {api_key_short_log}, txnId {transaction_id}: {transaction_error}",
                public_message="Failed to update usage count due to a server error.",
                status_code=500,
                log_exception=True
            )

        if transaction_result_container["limit_exceeded_in_txn"]:
            logger.warning(
                f"record_api_usage: Usage limit exceeded for key {api_key_short_log}, "
                f"not recording transaction {transaction_id}."
            )
            return create_error_response(
                internal_message=f"Usage limit exceeded for key {api_key_short_log}.",
                public_message="Usage limit exceeded.",
                status_code=429
            )

        if transaction_result_container["final_usage_count"] is not None:
            expires_at = datetime.now(timezone.utc) + timedelta(days=PROCESSED_TRANSACTION_TTL_DAYS)
            processed_txn_ref.set({
                "processedAt": firestore.SERVER_TIMESTAMP,
                "apiKeyIdentifier": api_key_short_log,
                "recordedUsageCount": transaction_result_container["final_usage_count"],
                "apiKeyDocId": key_doc_ref.id,
                "wasReset": transaction_result_container["was_reset_in_txn"],
                "expiresAt": expires_at
            })
            logger.info(
                f"record_api_usage: Successfully recorded usage for key {api_key_short_log}, "
                f"txnId {transaction_id}. Effective count: {transaction_result_container['final_usage_count']}"
            )
            return create_success_response(data={
                "status": "success",
                "message": "Usage recorded successfully.",
                "newEffectiveUsageCount": transaction_result_container["final_usage_count"]
            })
        else:
            logger.error(
                f"record_api_usage: Transaction for {api_key_short_log}, txnId {transaction_id} "
                "finished unexpectedly."
            )
            return create_error_response(
                internal_message=f"record_api_usage: Transaction for key {api_key_short_log}, txnId {transaction_id} "
                                 "finished without updating count or signaling known limit.",
                public_message="Internal server error during usage update processing.",
                status_code=500
            )

    except google_exceptions.RetryError as e:
        return create_error_response(
            internal_message=f"record_api_usage: Firestore transient error for {api_key_short_log}, txnId {transaction_id}: {e}",
            public_message="A transient database error occurred. Please try again.",
            status_code=503,
            log_exception=True
        )
    except Exception as e:
        return create_error_response(
            internal_message=f"record_api_usage: Unexpected error for {api_key_short_log}, txnId {transaction_id}: {e}",
            public_message="Internal Server Error.",
            status_code=500,
            log_exception=True
        )


@https_fn.on_request(cors=generate_api_key_cors_policy)
def generate_or_fetch_api_key(req: https_fn.Request) -> https_fn.Response:
    """
    IDトークンでユーザーを認証し、有効なAPIキーを返します。
    キーが存在しない場合は新しく生成して保存してから返します。
    """
    ensure_firebase_initialized()
    if db is None:
        return create_error_response(
            internal_message="generate_or_fetch_api_key: Firestore client not initialized.",
            public_message="Server configuration error.",
            status_code=500
        )

    logger.info("generate_or_fetch_api_key: Received request.")
    auth_header = req.headers.get("Authorization")
    id_token: str | None = None

    if auth_header and auth_header.startswith("Bearer "):
        id_token = auth_header.split("Bearer ", 1)[1]

    if not id_token:
        logger.warning("generate_or_fetch_api_key: Authorization header missing or invalid.")
        return create_error_response(
            internal_message="Authorization header missing or invalid format.",
            public_message="Unauthorized: Missing or invalid token.",
            status_code=401
        )

    try:
        try:
            decoded_token = auth.verify_id_token(id_token)
        except auth.RevokedIdTokenError:
            logger.warning("generate_or_fetch_api_key: ID token has been revoked.")
            return create_error_response(
                internal_message="ID token revoked.",
                public_message="Unauthorized: Token revoked.",
                status_code=401
            )
        except auth.UserDisabledError:
            logger.warning("generate_or_fetch_api_key: User account is disabled.")
            return create_error_response(
                internal_message="User account disabled.",
                public_message="Unauthorized: User disabled.",
                status_code=401
            )
        except auth.InvalidIdTokenError as token_error:
            logger.warning(f"generate_or_fetch_api_key: Invalid ID token: {token_error}")
            return create_error_response(
                internal_message=f"Invalid ID token: {token_error}",
                public_message="Unauthorized: Invalid token.",
                status_code=401
            )
        except Exception as auth_verify_error:
            return create_error_response(
                internal_message=f"generate_or_fetch_api_key: Token verification failed with unexpected auth error: {auth_verify_error}",
                public_message="Unauthorized: Token verification failed.",
                status_code=401,
                log_exception=True
            )

        uid: str | None = decoded_token.get('uid')
        email: str = decoded_token.get('email', '')

        if not uid:
            logger.error("generate_or_fetch_api_key: UID not found in a valid token. This should not happen.")
            return create_error_response(
                internal_message="UID not found in valid token.",
                public_message="Unauthorized: Invalid token claims.",
                status_code=401
            )

        logger.info(f"generate_or_fetch_api_key: Verified user. UID='{uid}', Email='{email}'")

        keys_collection_ref = db.collection("apiKeys")
        query = keys_collection_ref.where(
            filter=FieldFilter("user_uid", "==", uid)
        ).where(
            filter=FieldFilter("isEnabled", "==", True)
        ).order_by(
            "created_at", direction=firestore.Query.DESCENDING
        ).limit(1)

        active_key_docs = list(query.stream())

        if active_key_docs:
            existing_key_data = active_key_docs[0].to_dict()
            if existing_key_data is None:
                logger.error(f"generate_or_fetch_api_key: Existing key document {active_key_docs[0].id} has no data.")
                return create_error_response(
                    internal_message=f"Existing key document {active_key_docs[0].id} has no data.",
                    public_message="Internal Server Error: Key data inconsistency.",
                    status_code=500
                )

            api_key_value = existing_key_data.get("key")
            doc_id = active_key_docs[0].id

            if not api_key_value:
                logger.error(
                    f"generate_or_fetch_api_key: Data inconsistency - Doc {doc_id} "
                    f"for user {uid} is missing 'key' field."
                )
                return create_error_response(
                    internal_message=f"Data inconsistency for API key document {doc_id}.",
                    public_message="Internal Server Error: Key data inconsistency.",
                    status_code=500
                )

            api_key_short_log = api_key_value[:len(API_KEY_PREFIX) + 3] + "..."
            logger.info(f"generate_or_fetch_api_key: Found existing active API key for user {uid}: {api_key_short_log}")
            return create_success_response(
                data=api_key_value,
                status_code=200,
                content_type="text/plain"
            )
        else:
            logger.info(f"generate_or_fetch_api_key: No active API key found for user {uid}. Generating new one.")
            new_api_key_str = generate_api_key_string()
            api_key_short_log = new_api_key_str[:len(API_KEY_PREFIX) + 3] + "..."
            current_server_timestamp = firestore.SERVER_TIMESTAMP

            new_key_document_data = {
                "key": new_api_key_str,
                "user_uid": uid,
                "isEnabled": True,
                "usageCount": 0,
                "usageLimit": DEFAULT_USAGE_LIMIT,
                "lastReset": current_server_timestamp,
                "created_at": current_server_timestamp,
                "ownerEmail": email or "",
            }

            try:
                new_doc_ref = keys_collection_ref.document()
                new_doc_ref.set(new_key_document_data)
                logger.info(
                    f"generate_or_fetch_api_key: Successfully saved new API key for user {uid}: "
                    f"{api_key_short_log} (Doc ID: {new_doc_ref.id})"
                )
                return create_success_response(
                    data=new_api_key_str,
                    status_code=201,  # 201 Created
                    content_type="text/plain"
                )
            except Exception as db_write_err:
                return create_error_response(
                    internal_message=f"generate_or_fetch_api_key: Failed to save new API key to Firestore for user {uid}: {db_write_err}",
                    public_message="Internal Server Error: Could not save new API key.",
                    status_code=500,
                    log_exception=True
                )

    except google_exceptions.RetryError as e:
        return create_error_response(
            internal_message=f"generate_or_fetch_api_key: Firestore transient error: {e}",
            public_message="A transient database error occurred. Please try again.",
            status_code=503,
            log_exception=True
        )
    except Exception as e:
        return create_error_response(
            internal_message=f"generate_or_fetch_api_key: An unexpected critical error occurred: {e}",
            public_message="Internal Server Error.",
            status_code=500,
            log_exception=True
        )
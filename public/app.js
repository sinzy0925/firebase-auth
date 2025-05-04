// Firebaseサービスのインスタンスを取得
const auth = firebase.auth();
const db = firebase.firestore();
const provider = new firebase.auth.GoogleAuthProvider();


// DOM要素への参照を取得
const loginButton = document.getElementById('login-button');
const logoutButton = document.getElementById('logout-button');
const userInfoDiv = document.getElementById('user-info');
const userNameSpan = document.getElementById('user-name');
const userEmailSpan = document.getElementById('user-email');
const loginPromptDiv = document.getElementById('login-prompt');
const apiKeySection = document.getElementById('api-key-section');
const generateKeyButton = document.getElementById('generate-key-button');
const apiKeyListUl = document.getElementById('api-key-list');

let currentUid = null; // 現在ログインしているユーザーのUIDを保持

// --- 認証関連の関数 ---

// Googleログイン処理
const signInWithGoogle = () => {
    auth.signInWithPopup(provider)
        .then((result) => {
            console.log("ログイン成功:", result.user);
            // ログイン成功時のUI更新はonAuthStateChangedで行う
        })
        .catch((error) => {
            console.error("Googleログインエラー:", error);
            alert("ログインに失敗しました: " + error.message);
        });
};

// ログアウト処理
const signOutUser = () => {
    auth.signOut()
        .then(() => {
            console.log("ログアウト成功");
            // ログアウト成功時のUI更新はonAuthStateChangedで行う
        })
        .catch((error) => {
            console.error("ログアウトエラー:", error);
            alert("ログアウトに失敗しました: " + error.message);
        });
};

// 認証状態の変更を監視
auth.onAuthStateChanged((user) => {
    if (user) {
        // ユーザーがログインしている場合
        console.log("認証状態変更: ログイン中", user);
        currentUid = user.uid; // UIDを保持
        userNameSpan.textContent = user.displayName || '名前なし';
        userEmailSpan.textContent = user.email;
        userInfoDiv.style.display = 'block';
        loginPromptDiv.style.display = 'none';
        apiKeySection.style.display = 'block';
        loadApiKeys(); // ログインしたらAPIキーを読み込む
    } else {
        // ユーザーがログアウトしている場合
        console.log("認証状態変更: ログアウト");
        currentUid = null; // UIDをクリア
        userInfoDiv.style.display = 'none';
        loginPromptDiv.style.display = 'block';
        apiKeySection.style.display = 'none';
        apiKeyListUl.innerHTML = ''; // APIキーリストをクリア
    }
});

// --- APIキー管理関連の関数 ---

// APIキーを生成する（簡易版）
const generateApiKeyString = (length = 32) => {
    const characters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    let result = '';
    const charactersLength = characters.length;
    for (let i = 0; i < length; i++) {
        result += characters.charAt(Math.floor(Math.random() * charactersLength));
    }
    return `sk_${result}`; // Secret Keyのようなプレフィックスを付ける（任意）
};

// 新しいAPIキーを生成してFirestoreに保存
const generateApiKey = () => {
    if (!currentUid) {
        alert("ログインしていません。");
        return;
    }

    const newApiKey = generateApiKeyString();
    const timestamp = firebase.firestore.FieldValue.serverTimestamp(); // 作成日時

    // Firestoreに保存
    db.collection("apiKeys").add({
        key: newApiKey,
        user_uid: currentUid, // どのユーザーのキーか紐付ける
        created_at: timestamp,
        usageCount: 0,           // 初期値
        usageLimit: 100,         // デフォルト値 (またはユーザー設定値)
        lastReset: timestamp,    // 最初は作成時と同じで良い
        isEnabled: true,         // ★★★ これを追加 ★★★
        ownerEmail: firebase.auth().currentUser.email // ★★★ メールも追加 ★★★
    })
    .then((docRef) => {
        console.log("APIキーが生成・保存されました:", docRef.id);
        // UIに新しいキーを追加表示（loadApiKeysを再実行しても良い）
        addApiKeyToUI(newApiKey, docRef.id);
    })
    .catch((error) => {
        console.error("APIキーの保存エラー:", error);
        alert("APIキーの生成に失敗しました。");
    });
};

// FirestoreからAPIキーを読み込んで表示
const loadApiKeys = () => {
    console.log("loadApiKeys called with UID:", currentUid);
    if (!currentUid) return;

    apiKeyListUl.innerHTML = ''; // リストを一旦クリア

    db.collection("apiKeys")
      .where("user_uid", "==", currentUid) // 自分のUIDに紐づくキーのみ取得
      .orderBy("created_at", "desc") // 作成日時の降順で表示
      .get()
      .then((querySnapshot) => {
          if (querySnapshot.empty) {
              apiKeyListUl.innerHTML = '<li>まだAPIキーがありません。</li>';
              return;
          }
          querySnapshot.forEach((doc) => {
              addApiKeyToUI(doc.data().key, doc.id);
          });
      })
      .catch((error) => {
          console.error("APIキーの読み込みエラー: ", error);
          apiKeyListUl.innerHTML = '<li>APIキーの読み込みに失敗しました。</li>';
      });
};

// APIキーをUIリストに追加するヘルパー関数
const addApiKeyToUI = (key, docId) => {
    const li = document.createElement('li');
    li.setAttribute('data-id', docId); // ドキュメントIDを保持

    const keySpan = document.createElement('span');
    keySpan.textContent = key;

    const deleteButton = document.createElement('button');
    deleteButton.textContent = '削除';
    deleteButton.onclick = () => deleteApiKey(docId); // 削除ボタンにイベントを設定

    li.appendChild(keySpan);
    li.appendChild(deleteButton);

    // 「まだありません」の表示があれば削除
    const noKeyLi = apiKeyListUl.querySelector('li');
    if (noKeyLi && noKeyLi.textContent.includes('まだAPIキーがありません')) {
        apiKeyListUl.innerHTML = '';
    }

    apiKeyListUl.appendChild(li);
};


// APIキーを削除
const deleteApiKey = (docId) => {
    if (!currentUid) {
        alert("ログインしていません。");
        return;
    }
    if (!confirm("このAPIキーを削除してもよろしいですか？")) {
        return;
    }

    db.collection("apiKeys").doc(docId).delete()
    .then(() => {
        console.log("APIキーが削除されました:", docId);
        // UIから該当のリストアイテムを削除
        const itemToRemove = apiKeyListUl.querySelector(`li[data-id="${docId}"]`);
        if (itemToRemove) {
            itemToRemove.remove();
        }
        // もしリストが空になったらメッセージ表示
        if(apiKeyListUl.children.length === 0){
             apiKeyListUl.innerHTML = '<li>まだAPIキーがありません。</li>';
        }
    })
    .catch((error) => {
        console.error("APIキーの削除エラー:", error);
        alert("APIキーの削除に失敗しました。");
    });
};


// --- イベントリスナーの設定 ---
loginButton.addEventListener('click', signInWithGoogle);
logoutButton.addEventListener('click', signOutUser);
generateKeyButton.addEventListener('click', generateApiKey);
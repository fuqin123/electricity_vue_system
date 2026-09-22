import { computed, ref } from 'vue';
import { defineStore } from 'pinia';
import { login as apiLogin, logout as apiLogout, TOKEN_KEY, type LoginPayload } from '@eep/shared';

interface UserInfo {
  username: string;
  nickname: string;
  roleName: string;
  avatarText: string;
}

const USER_KEY = 'eep_user';

/** 认证仓库：token 与用户信息持久化到 localStorage */
export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(localStorage.getItem(TOKEN_KEY) ?? '');
  const user = ref<UserInfo | null>(readUser());
  const loading = ref(false);

  function readUser(): UserInfo | null {
    try {
      const raw = localStorage.getItem(USER_KEY);
      return raw ? (JSON.parse(raw) as UserInfo) : null;
    } catch {
      return null;
    }
  }

  const isLoggedIn = computed(() => Boolean(token.value));
  const displayName = computed(() => user.value?.nickname ?? user.value?.username ?? '未登录');

  async function login(payload: LoginPayload): Promise<void> {
    loading.value = true;
    try {
      const res = await apiLogin(payload);
      token.value = res.token;
      user.value = res.user;
      localStorage.setItem(TOKEN_KEY, res.token);
      localStorage.setItem(USER_KEY, JSON.stringify(res.user));
    } finally {
      loading.value = false;
    }
  }

  async function logout(): Promise<void> {
    try {
      await apiLogout();
    } finally {
      token.value = '';
      user.value = null;
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(USER_KEY);
    }
  }

  return { token, user, loading, isLoggedIn, displayName, login, logout };
});

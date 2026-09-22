<script setup lang="ts">
/**
 * LoginView —— 登录页
 *
 * ⚠ 正式环境要求：登录必须走公司统一身份认证（SSO），不允许独立账号体系。
 *   本页当前的账号 / 密码表单仅用于框架阶段的本地联调。
 *   接入 SSO 后，把 submit() 换成跳转 IdP 授权地址，并保留回调处理即可。
 */
import { reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElMessage, type FormInstance, type FormRules } from 'element-plus';
import { plantConfig } from '@eep/shared';
import { useAuthStore } from '@/stores/auth';
import BrandMark from '@/layout/BrandMark.vue';

const router = useRouter();
const route = useRoute();
const auth = useAuthStore();

const formRef = ref<FormInstance>();
const form = reactive({ username: 'admin', password: 'admin123', remember: true });

const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码长度不少于 6 位', trigger: 'blur' }
  ]
};

async function submit() {
  const ok = await formRef.value?.validate().catch(() => false);
  if (!ok) return;
  try {
    await auth.login({ username: form.username, password: form.password, remember: form.remember });
    ElMessage.success('登录成功');
    const redirect = (route.query.redirect as string) || '/dashboard';
    await router.replace(redirect);
  } catch (e) {
    ElMessage.error((e as Error).message || '登录失败，请检查账号或密码');
  }
}
</script>

<template>
  <div class="login">
    <div class="login__brand">
      <BrandMark :size="34" />
      <div>
        <h1>{{ plantConfig.shortName }}</h1>
        <p>{{ plantConfig.name }} · {{ plantConfig.code }}</p>
      </div>
    </div>

    <div class="login__panel">
      <div class="login__head">
        <h2>账号登录</h2>
        <p>数据驱动的能源与产效管理平台</p>
      </div>

      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" size="large" @submit.prevent="submit">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" placeholder="请输入用户名" clearable>
            <template #prefix><el-icon><User /></el-icon></template>
          </el-input>
        </el-form-item>

        <el-form-item label="密码" prop="password">
          <el-input v-model="form.password" type="password" placeholder="请输入密码" show-password @keyup.enter="submit">
            <template #prefix><el-icon><Lock /></el-icon></template>
          </el-input>
        </el-form-item>

        <div class="login__row">
          <el-checkbox v-model="form.remember">记住登录状态</el-checkbox>
          <el-link type="primary" :underline="false" @click="ElMessage.info('请联系系统管理员重置密码')">
            忘记密码？
          </el-link>
        </div>

        <el-button type="primary" size="large" class="login__submit" :loading="auth.loading" @click="submit">
          登 录
        </el-button>
      </el-form>

      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="框架阶段说明"
        description="当前为本地虚拟账号校验，任意账号 + 6 位以上密码均可登录；正式环境将替换为公司统一身份认证（SSO）。"
      />
    </div>

    <p class="login__foot">© 2026 {{ plantConfig.name }} · 系统仅限厂内内网访问</p>
  </div>
</template>

<style scoped lang="scss">
.login {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 24px;
  padding: 32px 16px;
  background:
    radial-gradient(1200px 520px at 12% -8%, #ebf1ff 0%, rgba(235, 241, 255, 0) 62%),
    radial-gradient(900px 480px at 92% 8%, #f3f7ff 0%, rgba(243, 247, 255, 0) 60%), #f7f9fb;
}

.login__brand {
  display: flex;
  align-items: center;
  gap: 14px;

  h1 {
    margin: 0;
    font-size: 22px;
    font-weight: 600;
    color: #0c0d0e;
    letter-spacing: 0.5px;
  }

  p {
    margin: 4px 0 0;
    font-size: 12px;
    color: $yl-text-subtle;
  }
}

.login__panel {
  width: 100%;
  max-width: 400px;
  padding: 28px 28px 24px;
  background: #ffffff;
  border: 1px solid $yl-border;
  border-radius: 8px;
  box-shadow: 0 5px 15px 0 rgba(12, 13, 14, 0.08), 0 2px 4px 0 rgba(12, 13, 14, 0.04);
}

.login__head {
  margin-bottom: 20px;

  h2 {
    margin: 0;
    font-size: 18px;
    font-weight: 600;
    color: #0c0d0e;
  }

  p {
    margin: 6px 0 0;
    font-size: 12px;
    color: $yl-text-subtle;
  }
}

.login__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: -4px 0 18px;
}

.login__submit {
  width: 100%;
  letter-spacing: 4px;
  margin-bottom: 16px;
}

.login__foot {
  margin: 0;
  font-size: 12px;
  color: $yl-text-subtle;
}
</style>

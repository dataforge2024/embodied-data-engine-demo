/** 登录页。用户输入凭据后前端持久化 token。 */

import { useState } from "react";
import { Button, Form, Input, message } from "antd";
import { login } from "../api/client";
import "./LoginPage.css";

interface LoginForm {
  username: string;
  password: string;
}

interface LoginPageProps {
  onSuccess: () => void;
}

export function LoginPage({ onSuccess }: LoginPageProps) {
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm<LoginForm>();

  const handleSubmit = async () => {
    let values: LoginForm;
    try {
      values = await form.validateFields();
    } catch {
      return;
    }

    setLoading(true);
    try {
      const response = await login(values.username, values.password);
      localStorage.setItem("rdh_access_token", response.access_token);
      localStorage.setItem("rdh_user", JSON.stringify(response.user));
      message.success("登录成功");
      onSuccess();
    } catch (error) {
      message.error(error instanceof Error ? error.message : "登录失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-header">
          <h1>RobotDataHub</h1>
          <p>具身智能数据采集平台</p>
        </div>
        <Form
          form={form}
          layout="vertical"
          onFinish={handleSubmit}
          autoComplete="off"
        >
          <Form.Item
            name="username"
            label="用户名"
            rules={[{ required: true, message: "请输入用户名" }]}
          >
            <Input size="large" placeholder="admin / operator" />
          </Form.Item>
          <Form.Item
            name="password"
            label="密码"
            rules={[{ required: true, message: "请输入密码" }]}
          >
            <Input.Password size="large" placeholder="demo-only-pass" />
          </Form.Item>
          <Form.Item>
            <Button
              type="primary"
              htmlType="submit"
              size="large"
              loading={loading}
              block
            >
              登录
            </Button>
          </Form.Item>
        </Form>
        <div className="login-hint">
          <p>Demo 凭据见 scripts/demo.py 第 145 行</p>
        </div>
      </div>
    </div>
  );
}

export class ApiClient {
  constructor(private base: string) {}
  async login(name: string, password: string) {
    return this.post("/login", { name, password });
  }
  private async post(path: string, body: unknown) {
    return fetch(this.base + path, { method: "POST", body: JSON.stringify(body) });
  }
}

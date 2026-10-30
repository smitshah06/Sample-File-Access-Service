import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";

export function Layout() {
  const { me, logout } = useAuth();
  const navigate = useNavigate();

  const signOut = async () => {
    await logout();
    navigate("/login");
  };

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">Sample File Access</span>
        <nav>
          <NavLink to="/samples">My samples</NavLink>
          {me?.role === "admin" && (
            <>
              <NavLink to="/admin/users">Users</NavLink>
              <NavLink to="/admin/samples">All samples</NavLink>
              <NavLink to="/admin/audit">Audit log</NavLink>
            </>
          )}
        </nav>
        <span className="who">
          {me?.username}
          {me?.role === "admin" && <span className="badge admin">admin</span>}
          <button className="link" onClick={signOut}>
            Sign out
          </button>
        </span>
      </header>
      <main>
        <Outlet />
      </main>
    </div>
  );
}

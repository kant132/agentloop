package testdata;

import javax.servlet.annotation.*;

@WebServlet(urlPatterns = {"/api/*"})
public class ServletExample {

    public String doGet() { return "get"; }
}

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const supabase = createClient(
  "https://xkchlqcwntcoakmyicqb.supabase.co",
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhrY2hscWN3bnRjb2FrbXlpY3FiIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjI4MDMyNzEsImV4cCI6MjA3ODM3OTI3MX0.702fpZLEHukvswrY0uwHknQYwdNDW-HeNsQxu_f1NnU"
);

window.logout = async function () {
  try {
    await supabase.auth.signOut();
    window.location.href = "login.html";
  } catch (err) {
    console.error("Logout error:", err);
    alert("Error logging out. Please try again.");
  }
};

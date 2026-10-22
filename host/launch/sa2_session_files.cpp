// sa2_session_files.cpp: the session recorder's files (sa2_session_files.h).
#include "sa2_session_files.h"

#include <algorithm>
#include <cstdio>
#include <sstream>

#include "sa2_platform.h"
#include "sha256.h"

#ifndef _WIN32
#include <sys/utsname.h>
#endif

namespace sa2s {
namespace {

std::string fold(const std::string& s) {
    std::string out = s;
    for (char& c : out)
        if (c >= 'A' && c <= 'Z') c = char(c - 'A' + 'a');
    return out;
}

// `root`/`rel` with each component matched case-blind, as the runtime resolves a guest path; ""
// when some component is absent (the launcher's find_folded, kept here so the Android session,
// which does not compile the launcher, has it too).
std::string find_folded(const std::string& root, const std::string& rel) {
    std::string at = root;
    size_t from = 0;
    while (from <= rel.size()) {
        const size_t slash = rel.find('/', from);
        const std::string part = rel.substr(from, slash == std::string::npos ? std::string::npos : slash - from);
        from = slash == std::string::npos ? rel.size() + 1 : slash + 1;
        if (part.empty()) continue;
        std::vector<std::string> names;
        if (!sa2p::list_dir(at, names)) return "";
        std::string hit;
        for (const std::string& n : names)
            if (fold(n) == fold(part)) hit = n;
        if (hit.empty()) return "";
        at += "/" + hit;
    }
    return at;
}

// The regular files directly in `dir` (none when it is absent), each under the name it has there.
void files_of(const std::string& dir, const char* source, std::vector<SeenFile>& out) {
    std::vector<std::string> names;
    if (dir.empty() || !sa2p::list_dir(dir, names)) return;
    for (const std::string& n : names) {
        sa2p::Stat st;
        if (!sa2p::stat_path(dir + "/" + n, st) || !st.is_file) continue;
        // The write root's file hides the read root's of the same folded name.
        auto same = std::find_if(out.begin(), out.end(), [&](const SeenFile& f) { return fold(f.name) == fold(n); });
        if (same != out.end()) *same = SeenFile{n, dir + "/" + n, source};
        else out.push_back(SeenFile{n, dir + "/" + n, source});
    }
}

}  // namespace

std::vector<SeenFile> seen_files(const std::string& read_root, const std::string& write_root, const std::string& rel,
                                 bool hide_read_root) {
    std::vector<SeenFile> found;
    if (!hide_read_root && !read_root.empty()) files_of(find_folded(read_root, rel), "game-folder", found);
    if (!write_root.empty()) files_of(find_folded(write_root, rel), "overlay", found);
    std::sort(found.begin(), found.end(), [](const SeenFile& a, const SeenFile& b) { return a.name < b.name; });
    return found;
}

Snapshot snapshot(const std::string& read_root, const std::string& write_root, const std::string& rel,
                  bool hide_read_root, const std::string& dest) {
    Snapshot s;
    if (!sa2p::make_dirs(dest)) {
        s.error = "cannot create " + dest;
        return s;
    }
    const std::vector<SeenFile> found = seen_files(read_root, write_root, rel, hide_read_root);
    std::string index = "# " + rel + " as the game sees it: name, source (overlay: the write root; game-folder: "
                        "the read root), bytes, sha256\n";
    for (const SeenFile& f : found) {
        std::string data;
        if (!sa2p::read_file(f.path, data) || !sa2p::write_file_atomic(dest + "/" + f.name, data)) {
            if (s.error.empty()) s.error = "cannot copy " + f.name;
            index += "# not copied: " + f.name + "\n";
            continue;
        }
        index += f.name + "\t" + f.source + "\t" + std::to_string(data.size()) + "\t" +
                 sha256_file(dest + "/" + f.name) + "\n";
        s.files++;
    }
    if (!s.error.empty()) index += "# error: " + s.error + "\n";
    if (!sa2p::write_file_atomic(dest + "/index.tsv", index) && s.error.empty()) s.error = "cannot write the index";
    return s;
}

std::string redact(const std::string& text, KV subs) {
    subs.erase(std::remove_if(subs.begin(), subs.end(), [](const auto& p) { return p.first.empty(); }), subs.end());
    std::stable_sort(subs.begin(), subs.end(),
                     [](const auto& a, const auto& b) { return a.first.size() > b.first.size(); });
    std::string out;
    out.reserve(text.size());
    for (size_t i = 0; i < text.size();) {
        bool hit = false;
        for (const auto& p : subs) {
            if (text.compare(i, p.first.size(), p.first) == 0) {
                out += p.second;
                i += p.first.size();
                hit = true;
                break;
            }
        }
        if (!hit) out += text[i++];
    }
    return out;
}

std::string env_text(const KV& env, const KV& subs) {
    std::string out;
    for (const auto& e : env)
        if (e.first.compare(0, 4, "SA2_") == 0) out += e.first + "=" + e.second + "\n";
    return redact(out, subs);
}

KV host_facts() {
    KV kv = {{"os", sa2p::os_name()}, {"arch", sa2p::arch_name()}, {"cpus", std::to_string(sa2p::cpu_count())}};
#ifndef _WIN32
    struct utsname u;
    if (uname(&u) == 0) kv.push_back({"kernel", std::string(u.sysname) + " " + u.release});
    std::string text;
    auto field = [&](const std::string& t, const std::string& key, char sep) -> std::string {
        std::istringstream in(t);
        std::string line;
        while (std::getline(in, line)) {
            if (line.compare(0, key.size(), key) != 0) continue;
            size_t at = line.find(sep);
            if (at == std::string::npos) continue;
            std::string v = line.substr(at + 1);
            v.erase(0, v.find_first_not_of(" \t\""));
            while (!v.empty() && (v.back() == '"' || v.back() == ' ' || v.back() == '\r')) v.pop_back();
            return v;
        }
        return "";
    };
    if (sa2p::read_file("/proc/cpuinfo", text, 1u << 20)) {
        std::string m = field(text, "model name", ':');
        if (m.empty()) m = field(text, "Hardware", ':');      // arm64 kernels name the SoC here
        if (!m.empty()) kv.push_back({"cpu_model", m});
    }
    if (sa2p::read_file("/proc/meminfo", text, 1u << 20)) {
        const std::string m = field(text, "MemTotal", ':');
        if (!m.empty()) kv.push_back({"memory", m});
    }
    if (sa2p::read_file("/etc/os-release", text, 1u << 16)) {
        const std::string m = field(text, "PRETTY_NAME", '=');
        if (!m.empty()) kv.push_back({"distribution", m});
    }
#endif
    return kv;
}

std::string json_quote(const std::string& s) {
    std::string out = "\"";
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') {
            out += '\\';
            out += char(c);
        } else if (c < 0x20) {
            char buf[8];
            std::snprintf(buf, sizeof buf, "\\u%04x", c);
            out += buf;
        } else {
            out += char(c);
        }
    }
    return out + "\"";
}

std::string json_object(const KV& kv) {
    std::string out = "{";
    for (size_t i = 0; i < kv.size(); i++) {
        const bool raw = !kv[i].first.empty() && kv[i].first.back() == ':';
        const std::string key = raw ? kv[i].first.substr(0, kv[i].first.size() - 1) : kv[i].first;
        out += (i ? ",\n " : "") + json_quote(key) + ": " + (raw ? kv[i].second : json_quote(kv[i].second));
    }
    return out + "}";
}

}  // namespace sa2s

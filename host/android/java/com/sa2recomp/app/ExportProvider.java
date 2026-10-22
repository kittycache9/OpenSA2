package com.sa2recomp.app;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;
import java.util.List;

/**
 * The Debug Export's files for the share sheet (DebugExport.shareIntent), in place of AndroidX's
 * FileProvider, which this Gradle-free build does not carry: content://com.sa2recomp.app.exports/
 * <name> is <cache>/exports/<name>, a ZIP DebugExport wrote, opened read-only. The provider is not
 * exported; the app picked in the share sheet reads the one URI the intent grants it
 * (FLAG_GRANT_READ_URI_PERMISSION, grantUriPermissions in the manifest), and nothing else: a name
 * with a path in it, or that is not an existing .zip there, is refused.
 */
public class ExportProvider extends ContentProvider {
    @Override
    public boolean onCreate() {
        return true;
    }

    private File file(Uri uri) throws FileNotFoundException {
        List<String> seg = uri.getPathSegments();
        if (getContext() == null || seg.size() != 1) throw new FileNotFoundException(String.valueOf(uri));
        String name = seg.get(0);
        if (name.contains("/") || name.startsWith(".") || !name.endsWith(".zip")) throw new FileNotFoundException(name);
        File f = new File(new File(getContext().getCacheDir(), "exports"), name);
        if (!f.isFile()) throw new FileNotFoundException(name);
        return f;
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (mode != null && !mode.equals("r")) throw new FileNotFoundException("read-only: " + mode);
        return ParcelFileDescriptor.open(file(uri), ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection, String[] args, String sort) {
        File f;
        try {
            f = file(uri);
        } catch (FileNotFoundException e) {
            return null;
        }
        String[] cols = projection != null ? projection : new String[] {OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE};
        MatrixCursor c = new MatrixCursor(cols, 1);
        Object[] row = new Object[cols.length];
        for (int i = 0; i < cols.length; i++) {
            if (OpenableColumns.DISPLAY_NAME.equals(cols[i])) row[i] = f.getName();
            else if (OpenableColumns.SIZE.equals(cols[i])) row[i] = f.length();
        }
        c.addRow(row);
        return c;
    }

    @Override
    public String getType(Uri uri) {
        return "application/zip";
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        throw new UnsupportedOperationException("read-only");
    }

    @Override
    public int delete(Uri uri, String selection, String[] args) {
        throw new UnsupportedOperationException("read-only");
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] args) {
        throw new UnsupportedOperationException("read-only");
    }
}

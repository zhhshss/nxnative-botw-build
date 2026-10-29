#ifndef NXK_ZIP_H
#define NXK_ZIP_H
/* Streaming ZIP32 writer. The caller owns this writer on one serial queue.
 * No input file is changed. On add failure call abort (close also aborts).
 * open initializes a fresh writer; do not reopen an already-open writer.
 * Archive names must be valid UTF-8 supplied by the caller.
 */
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#include <zlib.h>

#define NXK_ZIP_BUFFER_SIZE (256u * 1024u)
#define NXK_ZIP_MAX_ENTRIES 65535u

typedef bool (*NXKZipProgress)(void *context,uint64_t file_bytes,uint64_t file_total);
typedef struct {
    char *name;
    uint32_t crc,compressed,uncompressed,offset,mode;
    uint16_t name_length;
} NXKZipEntry;
typedef struct {
    int fd;
    bool failed;
    char error[256];
    char *path;
    dev_t device;
    ino_t inode;
    uint64_t offset,central_bytes;
    NXKZipEntry *entries;
    uint32_t count,capacity;
} NXKZipWriter;

static inline const char *nxk_zip_error(const NXKZipWriter *w) {
    return w->error[0]?w->error:"No ZIP error";
}
static inline bool nxk_zip_fail(NXKZipWriter *w,const char *format,...) {
    if (!w->failed) {
        va_list arguments; va_start(arguments,format);
        vsnprintf(w->error,sizeof(w->error),format,arguments);
        va_end(arguments);
    }
    w->failed=true;
    return false;
}
static inline void nxk_zip_u16(uint8_t *p,uint16_t value) {
    p[0]=(uint8_t)value; p[1]=(uint8_t)(value>>8);
}
static inline void nxk_zip_u32(uint8_t *p,uint32_t value) {
    nxk_zip_u16(p,(uint16_t)value); nxk_zip_u16(p+2,(uint16_t)(value>>16));
}
static inline bool nxk_zip_name_valid(const char *name) {
    if (!name || !*name || *name=='/' || strlen(name)>UINT16_MAX) return false;
    const char *component=name;
    for (const char *p=name;;p++) {
        unsigned char c=(unsigned char)*p;
        if (c=='\\' || c==':' || (c && (c<32 || c==127))) return false;
        if (c=='/' || !c) {
            size_t length=(size_t)(p-component);
            if (!length || (length==1 && component[0]=='.') ||
                (length==2 && component[0]=='.' && component[1]=='.')) return false;
            component=p+1;
        }
        if (!c) return true;
    }
}
static inline char *nxk_zip_copy_string(const char *value) {
    size_t length=strlen(value)+1;
    char *copy=(char *)malloc(length);
    if (copy) memcpy(copy,value,length);
    return copy;
}
static inline void nxk_zip_free_metadata(NXKZipWriter *w) {
    for (uint32_t i=0;i<w->count;i++) free(w->entries[i].name);
    free(w->entries); w->entries=NULL; w->count=0; w->capacity=0;
    free(w->path); w->path=NULL;
}
static inline void nxk_zip_remove_output(NXKZipWriter *w) {
    struct stat current;
    if (w->path && lstat(w->path,&current)==0 &&
        current.st_dev==w->device && current.st_ino==w->inode && S_ISREG(current.st_mode))
        unlink(w->path);
}
static inline void nxk_zip_abort(NXKZipWriter *w) {
    if (w->fd>=0) { close(w->fd); w->fd=-1; }
    nxk_zip_remove_output(w);
    nxk_zip_free_metadata(w);
}
static inline bool nxk_zip_open(NXKZipWriter *w,const char *path) {
    memset(w,0,sizeof(*w)); w->fd=-1;
    if (!path || !*path) return nxk_zip_fail(w,"Missing ZIP output path");
    w->path=nxk_zip_copy_string(path);
    if (!w->path) return nxk_zip_fail(w,"Allocating ZIP output path failed");
    w->fd=open(path,O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW|O_CLOEXEC,0600);
    if (w->fd<0) {
        nxk_zip_fail(w,"Creating ZIP output failed: %s",strerror(errno));
        nxk_zip_free_metadata(w); return false;
    }
    struct stat current;
    if (fstat(w->fd,&current)<0) {
        nxk_zip_fail(w,"Inspecting ZIP output failed: %s",strerror(errno));
        close(w->fd); w->fd=-1;
        /* This path was just created exclusively; no identity was available. */
        unlink(path); nxk_zip_free_metadata(w); return false;
    }
    w->device=current.st_dev; w->inode=current.st_ino;
    return true;
}
static inline bool nxk_zip_write(NXKZipWriter *w,const void *data,size_t size) {
    if (w->failed || w->fd<0) return nxk_zip_fail(w,"ZIP writer is not open");
    if ((uint64_t)size>=UINT32_MAX || w->offset+(uint64_t)size>=UINT32_MAX)
        return nxk_zip_fail(w,"ZIP32 output limit exceeded");
    const uint8_t *p=(const uint8_t *)data;
    size_t remaining=size;
    while (remaining) {
        ssize_t written=write(w->fd,p,remaining);
        if (written<0 && errno==EINTR) continue;
        if (written<=0) return nxk_zip_fail(w,"Writing ZIP failed: %s",written<0?strerror(errno):"short write");
        p+=written; remaining-=(size_t)written;
    }
    w->offset+=(uint64_t)size;
    return true;
}
static inline bool nxk_zip_same_file(const struct stat *before,const struct stat *after) {
    if (before->st_dev!=after->st_dev || before->st_ino!=after->st_ino ||
        before->st_size!=after->st_size || before->st_mode!=after->st_mode) return false;
#if defined(__APPLE__)
    return before->st_mtimespec.tv_sec==after->st_mtimespec.tv_sec &&
        before->st_mtimespec.tv_nsec==after->st_mtimespec.tv_nsec &&
        before->st_ctimespec.tv_sec==after->st_ctimespec.tv_sec &&
        before->st_ctimespec.tv_nsec==after->st_ctimespec.tv_nsec;
#else
    return before->st_mtim.tv_sec==after->st_mtim.tv_sec && before->st_mtim.tv_nsec==after->st_mtim.tv_nsec &&
        before->st_ctim.tv_sec==after->st_ctim.tv_sec && before->st_ctim.tv_nsec==after->st_ctim.tv_nsec;
#endif
}
/* source_path is only a consistency check for add_file; add_fd passes NULL.
 * pread starts at byte zero without changing a caller-owned fd's offset. */
static inline bool nxk_zip_add_source(NXKZipWriter *w,int input,const char *source_path,
                                     const char *archive_name,NXKZipProgress progress,void *context) {
    if (w->failed || w->fd<0) return nxk_zip_fail(w,"ZIP writer is not open");
    if (input<0 || !nxk_zip_name_valid(archive_name)) return nxk_zip_fail(w,"Invalid ZIP source fd or entry path");
    if (w->count>=NXK_ZIP_MAX_ENTRIES) return nxk_zip_fail(w,"ZIP32 entry count limit exceeded");
    for (uint32_t i=0;i<w->count;i++)
        if (!strcmp(w->entries[i].name,archive_name)) return nxk_zip_fail(w,"Duplicate ZIP entry path");
    struct stat before,after;
    z_stream stream={0};
    uint8_t *buffers=NULL;
    char *name=NULL;
    bool zinitialized=false,ok=false;
    uint64_t total=0,compressed_start=0;
    uint32_t checksum=(uint32_t)crc32(0L,Z_NULL,0);
    uint16_t name_length=(uint16_t)strlen(archive_name);
    uint64_t central_reserve=w->central_bytes+46u+name_length+22u;
    uint64_t local_offset=w->offset;
    if (fstat(input,&before)<0) { nxk_zip_fail(w,"Inspecting ZIP source failed: %s",strerror(errno)); goto cleanup; }
    if (!S_ISREG(before.st_mode) || before.st_size<0 || (uint64_t)before.st_size>=UINT32_MAX) {
        nxk_zip_fail(w,"ZIP source must be a regular file smaller than ZIP32 limit"); goto cleanup;
    }
    if (before.st_dev==w->device && before.st_ino==w->inode) {
        nxk_zip_fail(w,"ZIP cannot include its own output"); goto cleanup;
    }
    if (w->offset+30u+name_length+central_reserve>=UINT32_MAX) {
        nxk_zip_fail(w,"ZIP32 output limit exceeded"); goto cleanup;
    }
    if (w->count==w->capacity) {
        uint32_t capacity=w->capacity?w->capacity*2:16;
        if (capacity>NXK_ZIP_MAX_ENTRIES) capacity=NXK_ZIP_MAX_ENTRIES;
        NXKZipEntry *entries=(NXKZipEntry *)realloc(w->entries,(size_t)capacity*sizeof(*entries));
        if (!entries) { nxk_zip_fail(w,"Allocating ZIP directory failed"); goto cleanup; }
        w->entries=entries; w->capacity=capacity;
    }
    name=nxk_zip_copy_string(archive_name);
    buffers=(uint8_t *)malloc(2u*NXK_ZIP_BUFFER_SIZE);
    if (!name || !buffers) { nxk_zip_fail(w,"Allocating ZIP streaming buffers failed"); goto cleanup; }
    if (deflateInit2(&stream,Z_DEFAULT_COMPRESSION,Z_DEFLATED,-MAX_WBITS,8,Z_DEFAULT_STRATEGY)!=Z_OK) {
        nxk_zip_fail(w,"Initializing ZIP deflate failed"); goto cleanup;
    }
    zinitialized=true;
    uint8_t local[30]={0};
    nxk_zip_u32(local,0x04034b50); nxk_zip_u16(local+4,20);
    nxk_zip_u16(local+6,0x0800); nxk_zip_u16(local+8,8); nxk_zip_u16(local+12,0x21);
    nxk_zip_u16(local+26,name_length);
    if (!nxk_zip_write(w,local,sizeof(local)) || !nxk_zip_write(w,name,name_length)) goto cleanup;
    compressed_start=w->offset;
    if (progress && !progress(context,0,(uint64_t)before.st_size)) { nxk_zip_fail(w,"ZIP export cancelled"); goto cleanup; }
    for (;;) {
        ssize_t count=pread(input,buffers,NXK_ZIP_BUFFER_SIZE,(off_t)total);
        if (count<0 && errno==EINTR) continue;
        if (count<0) { nxk_zip_fail(w,"Reading ZIP source failed: %s",strerror(errno)); goto cleanup; }
        if (!count) break;
        total+=(uint64_t)count;
        if (total>(uint64_t)before.st_size || total>=UINT32_MAX) { nxk_zip_fail(w,"ZIP source changed while reading"); goto cleanup; }
        checksum=(uint32_t)crc32(checksum,buffers,(uInt)count);
        stream.next_in=buffers; stream.avail_in=(uInt)count;
        while (stream.avail_in) {
            stream.next_out=buffers+NXK_ZIP_BUFFER_SIZE; stream.avail_out=NXK_ZIP_BUFFER_SIZE;
            if (deflate(&stream,Z_NO_FLUSH)!=Z_OK) { nxk_zip_fail(w,"Compressing ZIP source failed"); goto cleanup; }
            size_t produced=NXK_ZIP_BUFFER_SIZE-stream.avail_out;
            if (w->offset+produced+central_reserve>=UINT32_MAX) { nxk_zip_fail(w,"ZIP32 output limit exceeded"); goto cleanup; }
            if (!nxk_zip_write(w,buffers+NXK_ZIP_BUFFER_SIZE,produced)) goto cleanup;
        }
        if (progress && !progress(context,total,(uint64_t)before.st_size)) { nxk_zip_fail(w,"ZIP export cancelled"); goto cleanup; }
    }
    for (;;) {
        stream.next_out=buffers+NXK_ZIP_BUFFER_SIZE; stream.avail_out=NXK_ZIP_BUFFER_SIZE;
        int result=deflate(&stream,Z_FINISH);
        if (result!=Z_OK && result!=Z_STREAM_END) { nxk_zip_fail(w,"Finishing ZIP deflate failed"); goto cleanup; }
        size_t produced=NXK_ZIP_BUFFER_SIZE-stream.avail_out;
        if (w->offset+produced+central_reserve>=UINT32_MAX) { nxk_zip_fail(w,"ZIP32 output limit exceeded"); goto cleanup; }
        if (!nxk_zip_write(w,buffers+NXK_ZIP_BUFFER_SIZE,produced)) goto cleanup;
        if (result==Z_STREAM_END) break;
    }
    if (fstat(input,&after)<0 || !nxk_zip_same_file(&before,&after) || total!=(uint64_t)before.st_size) {
        nxk_zip_fail(w,"ZIP source changed while reading"); goto cleanup;
    }
    struct stat path_stat;
    if (source_path && (lstat(source_path,&path_stat)<0 || !nxk_zip_same_file(&before,&path_stat))) {
        nxk_zip_fail(w,"ZIP source path changed while reading"); goto cleanup;
    }
    if (deflateEnd(&stream)!=Z_OK) { zinitialized=false; nxk_zip_fail(w,"Releasing ZIP deflate failed"); goto cleanup; }
    zinitialized=false;
    uint32_t compressed=(uint32_t)(w->offset-compressed_start);
    nxk_zip_u32(local+14,checksum); nxk_zip_u32(local+18,compressed); nxk_zip_u32(local+22,(uint32_t)total);
    if (lseek(w->fd,(off_t)local_offset,SEEK_SET)<0) { nxk_zip_fail(w,"Seeking ZIP header failed: %s",strerror(errno)); goto cleanup; }
    size_t remaining=sizeof(local); const uint8_t *cursor=local;
    while (remaining) {
        ssize_t written=write(w->fd,cursor,remaining);
        if (written<0 && errno==EINTR) continue;
        if (written<=0) { nxk_zip_fail(w,"Updating ZIP header failed"); goto cleanup; }
        remaining-=(size_t)written; cursor+=written;
    }
    if (lseek(w->fd,(off_t)w->offset,SEEK_SET)<0) { nxk_zip_fail(w,"Seeking ZIP payload failed: %s",strerror(errno)); goto cleanup; }
    w->entries[w->count++]=(NXKZipEntry){name,checksum,compressed,(uint32_t)total,(uint32_t)local_offset,
        (uint32_t)(S_IFREG | ((before.st_mode & 0111)?0755:0644)),name_length};
    w->central_bytes+=46u+name_length;
    name=NULL; ok=true;
cleanup:
    if (zinitialized) deflateEnd(&stream);
    free(name); free(buffers);
    return ok;
}
/* The caller retains the fd and may open it by a checked openat/O_NOFOLLOW
 * walk. No path is reopened, and the caller's seek position stays unchanged. */
static inline bool nxk_zip_add_fd(NXKZipWriter *w,int source_fd,const char *archive_name,
                                 NXKZipProgress progress,void *context) {
    return nxk_zip_add_source(w,source_fd,NULL,archive_name,progress,context);
}
static inline bool nxk_zip_add_file(NXKZipWriter *w,const char *source_path,const char *archive_name,
                                   NXKZipProgress progress,void *context) {
    if (w->failed || w->fd<0) return nxk_zip_fail(w,"ZIP writer is not open");
    if (!source_path) return nxk_zip_fail(w,"Missing ZIP source path");
    int input=open(source_path,O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK);
    if (input<0) return nxk_zip_fail(w,"Opening ZIP source failed: %s",strerror(errno));
    bool ok=nxk_zip_add_source(w,input,source_path,archive_name,progress,context);
    if (close(input)<0) return nxk_zip_fail(w,"Closing ZIP source failed: %s",strerror(errno));
    return ok;
}
static inline bool nxk_zip_close(NXKZipWriter *w) {
    if (w->failed || w->fd<0) {
        if (!w->failed) nxk_zip_fail(w,"ZIP writer is not open");
        nxk_zip_abort(w); return false;
    }
    uint64_t start=w->offset;
    if (start+w->central_bytes+22u>=UINT32_MAX) { nxk_zip_fail(w,"ZIP32 directory limit exceeded"); nxk_zip_abort(w); return false; }
    for (uint32_t i=0;i<w->count;i++) {
        NXKZipEntry *entry=&w->entries[i]; uint8_t central[46]={0};
        nxk_zip_u32(central,0x02014b50); nxk_zip_u16(central+4,0x0314); nxk_zip_u16(central+6,20);
        nxk_zip_u16(central+8,0x0800); nxk_zip_u16(central+10,8); nxk_zip_u16(central+14,0x21);
        nxk_zip_u32(central+16,entry->crc); nxk_zip_u32(central+20,entry->compressed); nxk_zip_u32(central+24,entry->uncompressed);
        nxk_zip_u16(central+28,entry->name_length); nxk_zip_u32(central+38,entry->mode<<16); nxk_zip_u32(central+42,entry->offset);
        if (!nxk_zip_write(w,central,sizeof(central)) || !nxk_zip_write(w,entry->name,entry->name_length)) { nxk_zip_abort(w); return false; }
    }
    uint8_t end[22]={0}; nxk_zip_u32(end,0x06054b50);
    nxk_zip_u16(end+8,(uint16_t)w->count); nxk_zip_u16(end+10,(uint16_t)w->count);
    nxk_zip_u32(end+12,(uint32_t)(w->offset-start)); nxk_zip_u32(end+16,(uint32_t)start);
    if (!nxk_zip_write(w,end,sizeof(end))) { nxk_zip_abort(w); return false; }
    if (fsync(w->fd)<0) { nxk_zip_fail(w,"Syncing ZIP output failed: %s",strerror(errno)); nxk_zip_abort(w); return false; }
    int fd=w->fd; w->fd=-1;
    if (close(fd)<0) { nxk_zip_fail(w,"Closing ZIP output failed: %s",strerror(errno)); nxk_zip_abort(w); return false; }
    nxk_zip_free_metadata(w);
    return true;
}
#endif

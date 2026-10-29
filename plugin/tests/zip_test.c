#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE
#include <assert.h>
#include <limits.h>
#include "../src/NXKZip.h"

static void put(const char *path,const void *bytes,size_t size) {
    FILE *file=fopen(path,"wb"); assert(file);
    assert(fwrite(bytes,1,size,file)==size); assert(fclose(file)==0);
}
typedef struct { uint64_t last,total; unsigned calls; const char *change; bool cancel; } Progress;
static bool progress(void *opaque,uint64_t done,uint64_t total) {
    Progress *p=(Progress *)opaque;
    assert(done>=p->last && done<=total); p->last=done; p->total=total; p->calls++;
    if (p->change && done) {
        FILE *file=fopen(p->change,"ab"); assert(file); assert(fputc(7,file)!=EOF); assert(fclose(file)==0); p->change=NULL;
    }
    return !(p->cancel && done);
}
int main(int argc,char **argv) {
    char temporary[]="/tmp/nxk-zip-test-XXXXXX";
    char *root=mkdtemp(temporary); assert(root);
    char text[PATH_MAX],binary[PATH_MAX],empty[PATH_MAX],output[PATH_MAX],bad[PATH_MAX],link[PATH_MAX],large[PATH_MAX];
    snprintf(text,sizeof(text),"%s/text.txt",root); snprintf(binary,sizeof(binary),"%s/binary.bin",root);
    snprintf(empty,sizeof(empty),"%s/empty",root); snprintf(output,sizeof(output),"%s/result.zip",root);
    snprintf(bad,sizeof(bad),"%s/rejected.zip",root); snprintf(link,sizeof(link),"%s/link",root);
    snprintf(large,sizeof(large),"%s/large",root);
    const char message[]="Streaming ZIP text\nUnicode names are provided by the caller.\n";
    put(text,message,sizeof(message)-1); put(empty,"",0);
    FILE *file=fopen(binary,"wb"); assert(file);
    for (unsigned i=0;i<700123;i++) assert(fputc((i*73u+(i>>8))&255,file)!=EOF);
    assert(fclose(file)==0);
    NXKZipWriter writer; assert(nxk_zip_open(&writer,output));
    Progress p={0};
    assert(nxk_zip_add_file(&writer,text,"metadata/text.txt",NULL,NULL));
    int input=open(binary,O_RDONLY|O_NOFOLLOW); assert(input>=0);
    assert(lseek(input,100,SEEK_SET)==100);
    assert(nxk_zip_add_fd(&writer,input,"romfs/Layout/binary.bin",progress,&p));
    assert(lseek(input,0,SEEK_CUR)==100); assert(close(input)==0);
    assert(p.calls>=4 && p.last==700123 && p.total==700123);
    assert(nxk_zip_add_file(&writer,empty,"empty",NULL,NULL));
    assert(nxk_zip_close(&writer)); assert(!writer.path && !writer.entries && writer.fd==-1);
    struct stat built; assert(stat(output,&built)==0 && built.st_size>22);
    assert(!nxk_zip_open(&writer,output)); nxk_zip_abort(&writer);
    struct stat preserved; assert(stat(output,&preserved)==0 && preserved.st_size==built.st_size);
    const char *unsafe[]={"/absolute","../up","a/../up","a\\b","a//b","./a","C:drive","a/","a\nb"};
    for (unsigned i=0;i<sizeof(unsafe)/sizeof(unsafe[0]);i++) {
        assert(nxk_zip_open(&writer,bad)); assert(!nxk_zip_add_file(&writer,text,unsafe[i],NULL,NULL));
        assert(!nxk_zip_close(&writer)); assert(access(bad,F_OK)!=0);
    }
    assert(symlink(text,link)==0);
    assert(nxk_zip_open(&writer,bad)); assert(!nxk_zip_add_file(&writer,link,"linked",NULL,NULL));
    nxk_zip_abort(&writer); assert(access(bad,F_OK)!=0);
    assert(nxk_zip_open(&writer,bad)); assert(!nxk_zip_add_file(&writer,root,"directory",NULL,NULL)); nxk_zip_abort(&writer);
    assert(nxk_zip_open(&writer,bad)); assert(nxk_zip_add_file(&writer,text,"same",NULL,NULL));
    assert(!nxk_zip_add_file(&writer,text,"same",NULL,NULL)); nxk_zip_abort(&writer);
    assert(nxk_zip_open(&writer,bad)); p=(Progress){.cancel=true};
    assert(!nxk_zip_add_file(&writer,binary,"cancelled",progress,&p));
    assert(strstr(nxk_zip_error(&writer),"cancelled")); nxk_zip_abort(&writer); assert(access(bad,F_OK)!=0);
    assert(nxk_zip_open(&writer,bad)); p=(Progress){.change=text};
    assert(!nxk_zip_add_file(&writer,text,"changed",progress,&p)); nxk_zip_abort(&writer);
    file=fopen(large,"wb"); assert(file); assert(ftruncate(fileno(file),(off_t)UINT32_MAX)==0); assert(fclose(file)==0);
    assert(nxk_zip_open(&writer,bad)); assert(!nxk_zip_add_file(&writer,large,"large",NULL,NULL)); nxk_zip_abort(&writer);
    assert(nxk_zip_open(&writer,bad)); writer.offset=(uint64_t)UINT32_MAX-10;
    assert(!nxk_zip_add_file(&writer,empty,"offset-limit",NULL,NULL)); nxk_zip_abort(&writer);
    assert(nxk_zip_open(&writer,bad)); assert(!nxk_zip_add_file(&writer,bad,"self",NULL,NULL)); nxk_zip_abort(&writer);
    /* An externally replaced output path is never deleted by abort. */
    assert(nxk_zip_open(&writer,bad)); assert(unlink(bad)==0); put(bad,"replacement",11);
    nxk_zip_abort(&writer); assert(stat(bad,&preserved)==0 && preserved.st_size==11); assert(unlink(bad)==0);
    /* A descriptor failure at close must remove the incomplete ZIP. */
    assert(nxk_zip_open(&writer,bad)); assert(close(writer.fd)==0);
    assert(!nxk_zip_close(&writer)); assert(access(bad,F_OK)!=0);
    if (argc==2) {
        assert(rename(output,argv[1])==0);
        printf("ZIP interoperability fixture: %s\n",argv[1]);
    } else assert(unlink(output)==0);
    assert(unlink(text)==0 && unlink(binary)==0 && unlink(empty)==0 && unlink(link)==0 && unlink(large)==0);
    assert(rmdir(root)==0);
    puts("ZIP checks passed: streaming text/binary/empty, progress, cancellation, source mutation, paths, symlinks, size limits and output cleanup.");
    return 0;
}
